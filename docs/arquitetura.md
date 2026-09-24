# Arquitetura

## Visão geral

O sistema tem três camadas, e cada uma pode ser trocada sem mexer nas outras:

| Camada | Responsabilidade | Não sabe nada sobre |
|---|---|---|
| **Biblioteca de prompts** (`prompts/*.yaml` + `prompt_library.py`) | O que dizer ao modelo | API, orquestração |
| **Adaptador de LLM** (`llm.py`) | Como chamar o modelo e tratar erros | Conteúdo dos prompts, ordem das etapas |
| **Pipeline** (`pipeline.py`) | Ordem das etapas, encadeamento e roteamento | Texto dos prompts, detalhes da API |

Os `schemas.py` são o contrato entre as três: o YAML declara qual schema produz, o
adaptador pede à API exatamente aquele schema, e o pipeline recebe um objeto tipado.

## Fluxo de dados

```
mensagem ─► validar_entrada()
          ─► [classificacao]  Classificacao(tipo_contato, area, confianca)
                 │  tipo ∈ {spam, fora_de_escopo} → ENCERRADA
          ─► [extracao]       Extracao(fatos, partes, datas + trecho, faltantes)
          ─► [urgencia]       AnaliseUrgencia(nivel, prazo, fundamentacao, riscos, acao)
          ─► loop até N tentativas:
                [resposta]    RespostaCliente(assunto, corpo, perguntas)
                verificar_resposta()  → violou? feedback, próxima tentativa
                [revisao]     Revisao(aprovado, violacoes, parecer)
                                → reprovou? feedback, próxima tentativa
          ─► FichaTriagem(status, todas as saídas, alertas, versões dos prompts)
```

O pipeline repassa a cada etapa **só o que ela precisa**. A etapa de resposta, por
exemplo, recebe o tipo e a área, mas não a justificativa da classificação nem o
raciocínio da urgência: menos contexto irrelevante significa menos chance do modelo
vazar informação interna para o cliente. Há um teste que garante isso.

## Decisões de design

**Structured outputs em vez de parsing de texto.** Cada etapa devolve um modelo Pydantic
validado pela própria API (`messages.parse(output_format=...)`). Enums fecham a taxonomia:
o modelo não consegue inventar uma área "direito digital" ou um nível "urgentíssimo". É o
principal mecanismo de consistência, já que os modelos atuais não aceitam `temperature`.

**Raciocínio interno, fundamentação na saída.** O modelo raciocina no seu pensamento
interno (adaptive thinking), cuja profundidade é controlada pelo `effort` de cada etapa.
A saída traz o veredito e uma **fundamentação** curta para o advogado auditar
(`Classificacao.justificativa`, `AnaliseUrgencia.fundamentacao`, `Revisao.parecer`): os
fatos e o critério que levaram à decisão, e não o processo de pensamento. Veja em
[Lições da execução real](#lições-da-execução-real) por que não pedimos
chain-of-thought no texto da resposta.

**Effort por etapa.** A classificação é simples e roda em `low`; a urgência, que tem o
maior custo de erro, roda em `high`. O parâmetro fica no YAML junto com o prompt, porque
é uma decisão sobre aquela tarefa.

**Guardrails determinísticos + revisão por LLM.** Código pega com certeza e custo zero
as violações óbvias ("causa ganha", "R$ 1.500") e o que é contável (limite de palavras
por canal). Nesse caso a chamada de revisão nem é feita. A revisão por LLM cuida do que exige interpretação (parecer jurídico disfarçado,
fatos que não estão na ficha, tom).

**Gerador e revisor compartilham as regras.** As regras da OAB ficam em um único
`partials/regras_comunicacao.txt`, incluído pelos dois prompts. Se uma regra muda, gerador
e revisor mudam juntos; um teste compara os dois blocos renderizados.

**Falha segura.** Recusa do modelo, truncamento, erro de rede ou saída inválida viram
`ErroLLM`. O pipeline captura esse erro e devolve a ficha com status `revisao_humana`,
preservando o que já foi produzido. Uma mensagem nunca se perde por causa de uma falha de
IA. O adaptador também ativa o fallback do servidor (`fallbacks: "default"`) nos modelos
que o suportam, para que uma recusa por política seja refeita em outro modelo na mesma
chamada.

**Human-in-the-loop por padrão em casos críticos.** Urgência crítica, confiança abaixo
de 0,6 ou resposta não aprovada após N tentativas impedem o status `pronta_para_envio`.
Esses limites ficam em `ConfigPipeline`.

**Defesa contra prompt injection em camadas.**
1. Todo texto externo entra delimitado por tags (`<mensagem_cliente>`) e passa pelo filtro
   `dados`, que troca `<` e `>` por `‹` e `›`. Assim o texto não consegue fechar a tag e
   se passar por instrução.
2. Cada system prompt diz explicitamente que o conteúdo delimitado é dado, não instrução.
3. O few-shot da classificação inclui um exemplo de tentativa de injeção, rotulado como spam.
4. A saída é um schema fechado, então mesmo um modelo manipulado não consegue responder
   "APROVADO" em texto livre.

**System prompt estável.** Os templates de system não podem usar variáveis de
requisição (a biblioteca rejeita isso no carregamento). Tudo que muda por chamada vai
no turno do usuário, o que mantém o system elegível para prompt caching quando o volume
justificar.

**Rastreabilidade.** Cada ficha registra a versão de cada prompt usado
(`versoes_prompts`), e o adaptador loga prompt, versão, modelo, tokens e `request_id`.
Quando um prompt mudar, dá para comparar fichas geradas antes e depois.

## Lições da execução real

A primeira execução contra a API revelou dois problemas que os testes offline não
podiam pegar. Ficam registrados porque mudaram o design.

**1. Pedir raciocínio "passo a passo" na saída é recusado.** A versão 1.0.0 do prompt de
urgência pedia um campo `raciocinio` com a análise passo a passo antes do veredito, o
padrão clássico de chain-of-thought. Com o modelo atual, a API recusou a chamada em todos
os casos (`stop_reason: "refusal"`, `stop_details.category: "reasoning_extraction"`): a
instrução foi interpretada como tentativa de extrair o raciocínio interno do modelo. Esse
tipo de recusa também não é refeito pelo fallback do servidor. A correção (versão 2.0.0 de
urgência, classificação e revisão) foi deixar o raciocínio no pensamento interno,
controlado pelo `effort`, e pedir na saída apenas uma fundamentação objetiva, depois do
veredito. Um teste (`test_prompt_nao_pede_raciocinio_na_saida`) impede a regressão.

**2. LLM não é bom em contar palavras.** O revisor reprovava respostas de WhatsApp por
"passar de 90 palavras", contando inclusive a linha de assunto, que nem é enviada nesse
canal. Duas correções: o pipeline passa ao revisor exatamente o texto que o cliente
recebe (`guardrails.texto_enviado`), e a contagem de palavras saiu do prompt de revisão e
foi para o guardrail em código, com folga sobre o alvo pedido ao gerador. A regra geral:
**o que é verificável por código não deve ser delegado ao modelo**.

**3. Guardrail em regex também erra, e para o lado oposto.** Na avaliação completa, as
únicas falhas de conformidade (2 de 24) vieram do guardrail, não do modelo: ele bloqueava
qualquer "R$" (inclusive o valor da dívida que o próprio cliente citou) e qualquer menção a
"dados bancários" (inclusive o aviso "não envie dados bancários"). As regras passaram a
olhar o contexto: valores só violam R2 perto de palavras como honorários, custas ou
indenização, e R5 ignora menções precedidas de negação. Os textos reais que geraram os
falsos positivos viraram casos de teste.

## Pontos de extensão

- **Outro provedor ou modelo:** implemente o protocolo `LLM` (`gerar(prompt) -> BaseModel`).
- **Nova etapa:** crie o YAML, o schema em `schemas.py` e chame `_chamar` no pipeline.
- **Outro escritório:** `ConfigPipeline(nome_escritorio=..., limiar_confianca=...)`.
- **Prompts específicos de um cliente:** `BibliotecaPrompts(diretorio=Path(...))` com uma
  cópia customizada da pasta `prompts/`.

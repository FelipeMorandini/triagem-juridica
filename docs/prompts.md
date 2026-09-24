# Catálogo de prompts

## Anatomia de um prompt

Cada arquivo em `src/triagem_juridica/prompts/` segue o mesmo formato:

```yaml
id: classificacao              # igual ao nome do arquivo
versao: 1.0.0                  # semver; registrada em cada ficha
descricao: ...
tecnicas: [...]                # documentação viva das técnicas usadas
schema_saida: Classificacao    # modelo Pydantic em schemas.py
effort: low                    # esforço de raciocínio do modelo para esta tarefa
max_tokens: 4000
variaveis: [canal, mensagem]   # contrato de entrada do template user
system: |                      # papel, regras, exemplos (estável entre chamadas)
  ...
user: |                        # dados da requisição, delimitados por tags
  ...
exemplos:                      # few-shot, validados contra schema_saida
  - entrada: ...
    saida: {...}
```

Ao carregar, a biblioteca rejeita o prompt se:

- o `id` for diferente do nome do arquivo, ou a versão não seguir semver;
- o `schema_saida` não existir;
- algum exemplo few-shot não passar na validação do schema;
- o template `user` usar variáveis diferentes das declaradas;
- o template `system` usar qualquer variável de requisição.

Na renderização, variável faltando ou sobrando é erro (`StrictUndefined`).

## Técnicas por prompt

| Técnica | classificacao | extracao | urgencia | resposta | revisao |
|---|:-:|:-:|:-:|:-:|:-:|
| Role prompting | ✓ | ✓ | ✓ | ✓ | ✓ |
| Delimitação com tags XML | ✓ | ✓ | ✓ | ✓ | ✓ |
| Structured output (schema Pydantic) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Few-shot | ✓ (4, com casos de fronteira) | ✓ | | | ✓ (aprovado + reprovado) |
| Raciocínio interno + fundamentação na saída | ✓ effort low | | ✓ effort high, procedimento em 3 passos | | ✓ checklist |
| Rubrica explícita | ✓ confiança | | ✓ níveis com sinais | | ✓ R1–R7, F1–F3 |
| Prompt chaining (consome etapa anterior) | | ✓ | ✓ | ✓ | ✓ |
| Grounding (evidência literal) | | ✓ trecho por data | | | ✓ trecho por violação |
| Anti-alucinação (null > inventar) | | ✓ | ✓ prazos | | ✓ fidelidade F1 |
| Assimetria de custo de erro | | | ✓ na dúvida, sobe | | |
| Reflexão com feedback | | | | ✓ recebe feedback | ✓ gera feedback |
| LLM-as-judge | | | | | ✓ |
| Defesa contra injection | ✓ exemplo dedicado | ✓ | ✓ | | ✓ |
| Partial compartilhado | | | | ✓ | ✓ |

### Por que cada técnica está onde está

- **Few-shot na classificação** porque a fronteira entre tipos é sutil ("dúvida geral" ×
  "novo caso", "cliente existente" × "novo caso"). Os exemplos foram escolhidos para
  mostrar essas fronteiras, e não casos óbvios.
- **Sem few-shot na urgência** porque um exemplo concreto ancoraria o modelo em um tipo
  de caso. Uma rubrica com sinais por nível generaliza melhor para as várias áreas do
  direito.
- **Grounding na extração** porque o advogado confia na ficha. Exigir o trecho literal
  de cada data torna a alucinação visível e verificável.
- **Assimetria na urgência** porque os erros não custam o mesmo: classificar um caso
  crítico como médio pode custar o direito do cliente, e o erro contrário custa uma
  ligação mais cedo. O prompt diz isso explicitamente e o avaliador mede "urgência
  segura" (nunca abaixo do mínimo).
- **LLM-as-judge com o mesmo partial** porque o revisor precisa julgar pelas mesmas
  regras que o gerador tentou seguir, e não por uma versão paralela que pode divergir.

## Como criar ou alterar um prompt

1. Crie ou edite o YAML. Para mudar o comportamento de um prompt existente, suba a versão:
   patch para ajuste de texto, minor para nova regra ou exemplo, major para mudança de
   variáveis ou schema.
2. Se for um novo formato de saída, crie o modelo em `schemas.py` e inclua-o na tupla
   que monta o dicionário `SCHEMAS`.
3. Rode `uv run triagem renderizar <id> --var ...` para ler o prompt como o modelo vai ler.
4. Rode `uv run pytest`: o carregamento valida o YAML e os testes parametrizados passam a
   cobrir o novo prompt.
5. Rode `uv run python evals/rodar_evals.py --repeticoes 3` e compare com a execução
   anterior em `evals/resultados/` antes de publicar a nova versão.

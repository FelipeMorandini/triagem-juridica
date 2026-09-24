"""Demonstração do pipeline SEM chamar a API.

As saídas do modelo abaixo são SIMULADAS (roteirizadas à mão com o `LLMFalso`) para
mostrar o fluxo, o encadeamento entre etapas e o ciclo de revisão com feedback. Para ver
saídas reais do modelo, use `triagem processar` com uma ANTHROPIC_API_KEY.

    uv run python examples/demo_offline.py
"""

from pathlib import Path

from triagem_juridica import LLMFalso, PipelineTriagem

MENSAGEM = (Path(__file__).parent / "mensagens" / "trabalhista_email.txt").read_text("utf-8")

roteiro = {
    "classificacao": [
        {
            "justificativa": "Relata demissão sem justa causa e verbas não pagas; pede orientação.",
            "tipo_contato": "novo_caso",
            "area": "trabalhista",
            "confianca": 0.96,
        }
    ],
    "extracao": [
        {
            "nome_cliente": "Ana Lima",
            "telefone": None,
            "email": "ana.lima@email.com",
            "resumo_fatos": "A cliente foi demitida sem justa causa após 4 anos. "
            "Não recebeu as verbas rescisórias nem a guia do seguro-desemprego.",
            "partes_contrarias": ["Transportadora Rápida"],
            "datas_relevantes": [
                {"descricao": "Demissão", "data": "2026-09-01", "trecho": "no dia 01/09"}
            ],
            "documentos_mencionados": ["carteira de trabalho"],
            "valores_mencionados": [],
            "informacoes_faltantes": ["Último salário", "Se assinou o TRCT"],
        }
    ],
    "urgencia": [
        {
            "fundamentacao": "Demissão em 2026-09-01, 22 dias atrás. Rescisão não paga. "
            "Sem audiência ou prazo processual. Rubrica: média.",
            "nivel": "media",
            "prazo_identificado": None,
            "riscos": ["Seguro-desemprego tem prazo para ser requerido"],
            "acao_recomendada": "Advogado trabalhista contatar em até 2 dias úteis.",
        }
    ],
    # 1ª tentativa viola R1 → o guardrail determinístico reprova sem chamar a revisão.
    "resposta": [
        {
            "assunto": "Sua rescisão",
            "corpo": "Olá, Ana! Seu caso é praticamente ganho. Equipe Morandini Advocacia",
            "perguntas_ao_cliente": [],
        },
        {
            "assunto": "Recebemos sua mensagem sobre a rescisão",
            "corpo": "Olá, Ana! Recebemos sua mensagem sobre a rescisão e a guia do "
            "seguro-desemprego que a Transportadora Rápida ainda não liberou. Um advogado "
            "da nossa equipe vai analisar e entrar em contato para agendar uma conversa.\n\n"
            "Para adiantar, pode nos contar:\n1) Qual era o seu último salário?\n"
            "2) Você chegou a assinar algum documento de rescisão?\n\n"
            "Equipe Morandini Advocacia",
            "perguntas_ao_cliente": [
                "Qual era o seu último salário?",
                "Você chegou a assinar algum documento de rescisão?",
            ],
        },
    ],
    "revisao": [{"aprovado": True, "violacoes": [], "parecer": "Aprovada, sem violações."}],
}

llm = LLMFalso(roteiro)
ficha = PipelineTriagem(llm).processar(MENSAGEM, canal="email")

print("Chamadas ao modelo:", " → ".join(llm.ids_chamados()))
segunda_resposta = [c for c in llm.chamadas if c.id == "resposta"][1]
print("\nFeedback enviado na 2ª tentativa:")
print(segunda_resposta.user.split("<feedback_revisao>")[1].split("</feedback_revisao>")[0])
print("Ficha final (saídas simuladas):")
print(ficha.model_dump_json(indent=2))

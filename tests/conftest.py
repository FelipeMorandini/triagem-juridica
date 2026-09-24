"""Fábricas de saídas válidas para roteirizar o LLMFalso."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from triagem_juridica.prompt_library import BibliotecaPrompts

RECEBIDO_EM = datetime(2026, 9, 23, 10, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))

MENSAGEM_TRABALHISTA = (
    "Olá, meu nome é Ana Lima. Fui demitida da Transportadora Rápida em 01/09 sem justa "
    "causa e até hoje não recebi as verbas rescisórias. Tenho a carteira assinada."
)


def classificacao(**sobrescrever):
    base = {
        "justificativa": "Relata demissão e verbas não pagas.",
        "tipo_contato": "novo_caso",
        "area": "trabalhista",
        "confianca": 0.95,
    }
    return base | sobrescrever


def extracao(**sobrescrever):
    base = {
        "nome_cliente": "Ana Lima",
        "telefone": None,
        "email": None,
        "resumo_fatos": "A cliente foi demitida sem justa causa e não recebeu as verbas.",
        "partes_contrarias": ["Transportadora Rápida"],
        "datas_relevantes": [
            {"descricao": "Demissão", "data": "2026-09-01", "trecho": "demitida ... em 01/09"}
        ],
        "documentos_mencionados": ["carteira de trabalho"],
        "valores_mencionados": [],
        "informacoes_faltantes": ["Data de admissão", "Último salário"],
    }
    return base | sobrescrever


def urgencia(**sobrescrever):
    base = {
        "fundamentacao": "Demissão há 22 dias, rescisão não paga, sem prazo iminente.",
        "nivel": "media",
        "prazo_identificado": None,
        "riscos": ["Prescrição bienal a partir de 2026-09-01"],
        "acao_recomendada": "Contatar em até 2 dias úteis e pedir TRCT.",
    }
    return base | sobrescrever


def resposta(**sobrescrever):
    base = {
        "assunto": "Recebemos sua mensagem sobre a rescisão",
        "corpo": (
            "Olá, Ana! Recebemos sua mensagem sobre as verbas rescisórias da Transportadora "
            "Rápida. Um advogado da nossa equipe vai analisar e entrar em contato para "
            "agendar uma conversa.\n\nPara adiantar:\n1) Qual a data de admissão?\n"
            "2) Qual era o último salário?\n\nEquipe Morandini Advocacia"
        ),
        "perguntas_ao_cliente": ["Qual a data de admissão?", "Qual era o último salário?"],
    }
    return base | sobrescrever


def revisao_aprovada():
    return {"aprovado": True, "violacoes": [], "parecer": "Aprovada, sem violações."}


def revisao_reprovada(regra="R3", trecho="você tem direito a", correcao="Remover parecer."):
    return {
        "aprovado": False,
        "violacoes": [{"regra": regra, "trecho": trecho, "correcao": correcao}],
        "parecer": f"Reprovada por {regra}.",
    }


def roteiro_feliz(**sobrescrever):
    roteiro = {
        "classificacao": [classificacao()],
        "extracao": [extracao()],
        "urgencia": [urgencia()],
        "resposta": [resposta()],
        "revisao": [revisao_aprovada()],
    }
    return roteiro | sobrescrever


@pytest.fixture(scope="session")
def biblioteca() -> BibliotecaPrompts:
    return BibliotecaPrompts()

import pytest

from triagem_juridica.guardrails import (
    MAX_CARACTERES_ENTRADA,
    EntradaInvalida,
    texto_enviado,
    validar_entrada,
    verificar_resposta,
)
from triagem_juridica.schemas import RespostaCliente

from .conftest import resposta


def _resposta_com(corpo: str) -> RespostaCliente:
    return RespostaCliente.model_validate(resposta(corpo=corpo))


def test_entrada_valida_e_normalizada():
    assert validar_entrada("  oi, preciso de ajuda \n") == "oi, preciso de ajuda"


@pytest.mark.parametrize("mensagem", ["", "   ", "\n\t"])
def test_entrada_vazia_e_rejeitada(mensagem):
    with pytest.raises(EntradaInvalida, match="vazia"):
        validar_entrada(mensagem)


def test_entrada_longa_demais_e_rejeitada():
    with pytest.raises(EntradaInvalida, match="excede"):
        validar_entrada("a" * (MAX_CARACTERES_ENTRADA + 1))


def test_resposta_correta_passa():
    assert verificar_resposta(_resposta_com(resposta()["corpo"])) == []


@pytest.mark.parametrize(
    ("trecho", "regra"),
    [
        ("Seu caso é praticamente ganho.", "R1"),
        ("É causa ganha!", "R1"),
        ("Com certeza vamos conseguir.", "R1"),
        ("Garantimos a indenização.", "R1"),
        ("Você tem 90% de chance.", "R1"),
        ("A consulta custa R$ 300.", "R2"),
        ("Você pode receber até R$ 10.000.", "R2"),
        ("R$ 5 mil de indenização é o comum.", "R2"),
        ("Nossos honorários começam baixos.", "R2"),
        ("Mande sua senha do gov.br.", "R5"),
        ("Envie seus dados bancários.", "R5"),
        ("Informe sua senha.", "R5"),
    ],
)
def test_violacoes_sao_detectadas(trecho, regra):
    violacoes = verificar_resposta(_resposta_com(f"Olá! {trecho} Equipe"))
    assert len(violacoes) == 1
    assert violacoes[0].startswith(regra)


@pytest.mark.parametrize(
    "trecho",
    [
        "Vamos garantir que um advogado fale com você ainda hoje.",
        "Com certeza de que recebemos sua mensagem, respondemos em breve.",
        "Você mencionou um prejuízo; por favor, detalhe o ocorrido.",
        # Falsos positivos reais encontrados na avaliação com o modelo:
        "Seu nome foi negativado por uma dívida de R$ 890 de um plano já cancelado.",
        "Por segurança, não envie por este canal senhas, dados bancários ou documentos.",
    ],
)
def test_frases_legitimas_nao_geram_falso_positivo(trecho):
    assert verificar_resposta(_resposta_com(trecho)) == []


def test_limite_de_palavras_depende_do_canal():
    corpo = "palavra " * 150
    assert verificar_resposta(_resposta_com(corpo), "email") == []
    violacoes = verificar_resposta(_resposta_com(corpo), "whatsapp")
    assert violacoes == ["F2: corpo com 150 palavras excede o limite do canal whatsapp"]
    assert verificar_resposta(_resposta_com("palavra " * 300), "email")[0].startswith("F2")


def test_whatsapp_nao_envia_assunto():
    r = RespostaCliente.model_validate(resposta(assunto="Causa ganha"))
    assert verificar_resposta(r, "whatsapp") == []
    assert texto_enviado(r, "whatsapp") == r.corpo
    assert texto_enviado(r, "email").startswith("Assunto: Causa ganha")


def test_assunto_tambem_e_verificado():
    r = RespostaCliente.model_validate(resposta(assunto="Causa ganha para você"))
    assert verificar_resposta(r)[0].startswith("R1")

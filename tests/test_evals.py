"""Testes do próprio avaliador (offline) e teste de fumaça contra a API real (live)."""

import os

import pytest

from triagem_juridica import AnthropicLLM, LLMFalso, PipelineTriagem
from triagem_juridica.schemas import StatusTriagem

from .conftest import RECEBIDO_EM, roteiro_feliz, urgencia

try:
    from evals.rodar_evals import avaliar_ficha, carregar_casos
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip("evals/ fora do path", allow_module_level=True)


def test_golden_set_bem_formado():
    casos = carregar_casos()
    assert len(casos) >= 10
    assert len({c["id"] for c in casos}) == len(casos)
    tipos = {c["esperado"]["tipo_contato"] for c in casos}
    assert tipos == {"novo_caso", "cliente_existente", "duvida_geral", "spam", "fora_de_escopo"}
    assert any(c.get("fumaca") for c in casos)


def _ficha(roteiro):
    return PipelineTriagem(LLMFalso(roteiro)).processar("mensagem", recebido_em=RECEBIDO_EM)


def test_avaliador_aprova_ficha_que_atende_o_esperado():
    caso = {
        "esperado": {"tipo_contato": "novo_caso", "area": "trabalhista", "urgencia_minima": "media"}
    }
    notas = avaliar_ficha(caso, _ficha(roteiro_feliz()))
    assert notas == {"tipo": True, "area": True, "urgencia": True, "conformidade": True}


def test_avaliador_aceita_urgencia_acima_do_minimo_e_reprova_abaixo():
    caso = {"esperado": {"tipo_contato": "novo_caso", "urgencia_minima": "alta"}}
    acima = _ficha(roteiro_feliz(urgencia=[urgencia(nivel="critica")]))
    abaixo = _ficha(roteiro_feliz(urgencia=[urgencia(nivel="media")]))
    assert avaliar_ficha(caso, acima)["urgencia"] is True
    assert avaliar_ficha(caso, abaixo)["urgencia"] is False
    assert avaliar_ficha(caso, abaixo)["area"] is None


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requer ANTHROPIC_API_KEY")
@pytest.mark.parametrize("caso", carregar_casos(apenas_fumaca=True), ids=lambda c: c["id"])
def test_fumaca_api_real(caso):
    ficha = PipelineTriagem(AnthropicLLM()).processar(caso["mensagem"], canal=caso["canal"])
    notas = avaliar_ficha(caso, ficha)
    assert all(ok is not False for ok in notas.values()), (notas, ficha.alertas)
    if caso["esperado"]["tipo_contato"] == "spam":
        assert ficha.status is StatusTriagem.ENCERRADA

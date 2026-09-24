"""Testa o adaptador da API sem rede, com um cliente Anthropic simulado."""

from types import SimpleNamespace

import pytest

from triagem_juridica.llm import AnthropicLLM, ErroLLM, LLMFalso, RecusaDoModelo, RespostaTruncada
from triagem_juridica.schemas import Classificacao

from .conftest import classificacao


class ClienteSimulado:
    def __init__(self, stop_reason="end_turn", parsed=None):
        self.kwargs = None
        self._resposta = SimpleNamespace(
            stop_reason=stop_reason,
            parsed_output=parsed,
            model="claude-opus-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            _request_id="req_teste",
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.kwargs = kwargs
        return self._resposta


@pytest.fixture
def prompt(biblioteca):
    return biblioteca.renderizar("classificacao", canal="email", mensagem="Fui demitido.")


def test_envia_prompt_schema_e_effort(prompt):
    saida = Classificacao.model_validate(classificacao())
    cliente = ClienteSimulado(parsed=saida)

    assert AnthropicLLM(cliente, modelo="claude-opus-5").gerar(prompt) is saida
    kwargs = cliente.kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["system"] == prompt.system
    assert kwargs["messages"] == [{"role": "user", "content": prompt.user}]
    assert kwargs["output_format"] is Classificacao
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] == prompt.max_tokens


def test_fallback_apenas_em_modelos_suportados(prompt):
    saida = Classificacao.model_validate(classificacao())
    com = ClienteSimulado(parsed=saida)
    AnthropicLLM(com, modelo="claude-opus-5").gerar(prompt)
    assert com.kwargs["fallbacks"] == "default"

    sem = ClienteSimulado(parsed=saida)
    AnthropicLLM(sem, modelo="claude-sonnet-5").gerar(prompt)
    assert "fallbacks" not in sem.kwargs and "betas" not in sem.kwargs


def test_modelo_configuravel_por_ambiente(monkeypatch):
    monkeypatch.setenv("TRIAGEM_MODELO", "claude-sonnet-5")
    assert AnthropicLLM(ClienteSimulado()).modelo == "claude-sonnet-5"


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "erro"),
    [
        ("refusal", None, RecusaDoModelo),
        ("max_tokens", None, RespostaTruncada),
        ("end_turn", None, ErroLLM),
    ],
)
def test_respostas_invalidas_viram_erro_tipado(prompt, stop_reason, parsed, erro):
    with pytest.raises(erro):
        AnthropicLLM(ClienteSimulado(stop_reason, parsed)).gerar(prompt)


def test_llm_falso_valida_dicts_contra_o_schema(prompt):
    llm = LLMFalso({"classificacao": [classificacao(area="astrologia")]})
    with pytest.raises(ValueError):
        llm.gerar(prompt)


def test_llm_falso_acusa_roteiro_incompleto(prompt):
    with pytest.raises(AssertionError, match="sem resposta"):
        LLMFalso({}).gerar(prompt)

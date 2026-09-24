"""Camada de acesso ao LLM.

O pipeline depende apenas do protocolo `LLM`. Em produção usamos `AnthropicLLM`; nos
testes usamos `LLMFalso`, que devolve respostas roteirizadas. Assim o pipeline inteiro é
testável de forma determinística, sem rede e sem custo.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel

from triagem_juridica.prompt_library import PromptRenderizado

logger = logging.getLogger(__name__)

MODELO_PADRAO = "claude-opus-5"
# Fallback no servidor: se o modelo recusar por política, a API refaz a chamada em outro
# modelo dentro da mesma requisição. Só vale para os modelos que suportam o parâmetro.
BETA_FALLBACK = "server-side-fallback-2026-07-01"
MODELOS_COM_FALLBACK = {"claude-opus-5", "claude-fable-5-1"}


class ErroLLM(Exception):
    """Falha ao obter uma saída válida do modelo."""


class RecusaDoModelo(ErroLLM):
    pass


class RespostaTruncada(ErroLLM):
    pass


class LLM(Protocol):
    def gerar(self, prompt: PromptRenderizado) -> BaseModel: ...


class AnthropicLLM:
    def __init__(self, cliente: anthropic.Anthropic | None = None, modelo: str | None = None):
        self._cliente = cliente or anthropic.Anthropic()
        self.modelo = modelo or os.environ.get("TRIAGEM_MODELO", MODELO_PADRAO)

    def gerar(self, prompt: PromptRenderizado) -> BaseModel:
        extras: dict[str, Any] = {}
        if self.modelo in MODELOS_COM_FALLBACK:
            extras = {"betas": [BETA_FALLBACK], "fallbacks": "default"}

        try:
            resposta = self._cliente.beta.messages.parse(
                model=self.modelo,
                max_tokens=prompt.max_tokens,
                system=prompt.system,
                messages=[{"role": "user", "content": prompt.user}],
                output_format=prompt.schema,
                output_config={"effort": prompt.effort},
                **extras,
            )
        except anthropic.APIStatusError as erro:
            raise ErroLLM(f"{prompt.id}: API retornou {erro.status_code}: {erro.message}") from erro
        except anthropic.APIConnectionError as erro:
            raise ErroLLM(f"{prompt.id}: falha de conexão com a API") from erro

        logger.info(
            "prompt=%s versao=%s modelo=%s stop=%s tokens_in=%s tokens_out=%s request_id=%s",
            prompt.id,
            prompt.versao,
            resposta.model,
            resposta.stop_reason,
            resposta.usage.input_tokens,
            resposta.usage.output_tokens,
            resposta._request_id,
        )

        if resposta.stop_reason == "refusal":
            raise RecusaDoModelo(f"{prompt.id}: o modelo recusou a solicitação")
        if resposta.stop_reason == "max_tokens":
            raise RespostaTruncada(f"{prompt.id}: resposta atingiu max_tokens={prompt.max_tokens}")
        if resposta.parsed_output is None:
            raise ErroLLM(f"{prompt.id}: saída estruturada ausente")
        return resposta.parsed_output


class LLMFalso:
    """LLM roteirizado para testes: cada prompt id tem uma fila de respostas.

    Um item da fila pode ser uma instância do schema, um dict (validado contra o schema do
    prompt, como a API faria) ou uma exceção a ser lançada.
    """

    def __init__(self, roteiro: Mapping[str, Iterable[BaseModel | dict | Exception]]):
        self._filas: dict[str, deque] = defaultdict(deque)
        for prompt_id, respostas in roteiro.items():
            self._filas[prompt_id].extend(respostas)
        self.chamadas: list[PromptRenderizado] = []

    def gerar(self, prompt: PromptRenderizado) -> BaseModel:
        self.chamadas.append(prompt)
        fila = self._filas[prompt.id]
        if not fila:
            raise AssertionError(f"LLMFalso sem resposta roteirizada para {prompt.id!r}")
        item = fila.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return prompt.schema.model_validate(item)
        return item

    def ids_chamados(self) -> list[str]:
        return [chamada.id for chamada in self.chamadas]

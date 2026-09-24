"""Biblioteca de prompts versionados.

Cada prompt vive em um arquivo YAML em `prompts/` com metadados (id, versão, técnicas,
schema de saída, parâmetros do modelo), templates Jinja para system e user, e exemplos
few-shot. A biblioteca carrega, valida e renderiza esses arquivos. Nenhum texto de prompt
fica espalhado pelo código Python.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import jinja2
import jinja2.meta
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from triagem_juridica.schemas import SCHEMAS

DIRETORIO_PADRAO = Path(__file__).parent / "prompts"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ErroPrompt(Exception):
    """Prompt inválido ou renderizado com variáveis incorretas."""


def neutralizar_tags(texto: str) -> str:
    """Impede que texto externo feche ou abra tags XML do prompt.

    Todo conteúdo vindo do cliente é delimitado por tags (ex.: <mensagem_cliente>).
    Se o texto contiver `</mensagem_cliente>` ele poderia "escapar" da delimitação
    e se passar por instrução. Trocamos os sinais angulares por equivalentes visuais.
    """
    return texto.replace("<", "‹").replace(">", "›")


class Exemplo(BaseModel):
    """Exemplo few-shot: entrada do cliente e a saída esperada do modelo."""

    entrada: str
    saida: dict[str, Any]


class PromptSpec(BaseModel):
    id: str
    versao: str
    descricao: str
    tecnicas: list[str] = Field(min_length=1)
    schema_saida: str
    effort: Effort
    max_tokens: int = Field(gt=0)
    variaveis: list[str]
    system: str
    user: str
    exemplos: list[Exemplo] = Field(default_factory=list)

    @field_validator("versao")
    @classmethod
    def _versao_semantica(cls, valor: str) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", valor):
            raise ValueError(f"versão deve seguir semver (X.Y.Z), recebido {valor!r}")
        return valor

    @field_validator("schema_saida")
    @classmethod
    def _schema_existe(cls, valor: str) -> str:
        if valor not in SCHEMAS:
            raise ValueError(f"schema_saida desconhecido: {valor!r}")
        return valor

    @model_validator(mode="after")
    def _exemplos_respeitam_schema(self) -> PromptSpec:
        # Um exemplo few-shot fora do contrato ensina o modelo a errar.
        for i, exemplo in enumerate(self.exemplos):
            try:
                self.modelo_saida.model_validate(exemplo.saida)
            except ValueError as erro:
                raise ValueError(f"exemplo {i} não respeita {self.schema_saida}: {erro}") from erro
        return self

    @property
    def modelo_saida(self) -> type[BaseModel]:
        return SCHEMAS[self.schema_saida]


@dataclass(frozen=True)
class PromptRenderizado:
    id: str
    versao: str
    system: str
    user: str
    schema: type[BaseModel]
    effort: Effort
    max_tokens: int


class BibliotecaPrompts:
    def __init__(self, diretorio: Path = DIRETORIO_PADRAO) -> None:
        self._diretorio = diretorio
        self._env = jinja2.Environment(
            # O loader permite `{% include "partials/..." %}`: trechos compartilhados
            # entre prompts (ex.: regras da OAB usadas pelo gerador e pelo revisor).
            loader=jinja2.FileSystemLoader(diretorio),
            undefined=jinja2.StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._env.filters["dados"] = neutralizar_tags

    @cached_property
    def _specs(self) -> dict[str, PromptSpec]:
        specs: dict[str, PromptSpec] = {}
        for arquivo in sorted(self._diretorio.glob("*.yaml")):
            bruto = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
            try:
                spec = PromptSpec.model_validate(bruto)
            except ValueError as erro:
                raise ErroPrompt(f"{arquivo.name}: {erro}") from erro
            if spec.id != arquivo.stem:
                raise ErroPrompt(f"{arquivo.name}: id {spec.id!r} difere do nome do arquivo")
            self._validar_variaveis(spec)
            specs[spec.id] = spec
        if not specs:
            raise ErroPrompt(f"nenhum prompt encontrado em {self._diretorio}")
        return specs

    def ids(self) -> list[str]:
        return list(self._specs)

    def get(self, prompt_id: str) -> PromptSpec:
        try:
            return self._specs[prompt_id]
        except KeyError:
            raise ErroPrompt(f"prompt desconhecido: {prompt_id!r}") from None

    def renderizar(self, prompt_id: str, **variaveis: Any) -> PromptRenderizado:
        spec = self.get(prompt_id)
        declaradas = set(spec.variaveis)
        recebidas = set(variaveis)
        if faltando := declaradas - recebidas:
            raise ErroPrompt(f"{prompt_id}: variáveis faltando: {sorted(faltando)}")
        if extras := recebidas - declaradas:
            raise ErroPrompt(f"{prompt_id}: variáveis não declaradas: {sorted(extras)}")

        exemplos = [
            {"entrada": ex.entrada, "saida": json.dumps(ex.saida, ensure_ascii=False, indent=2)}
            for ex in spec.exemplos
        ]
        system = self._env.from_string(spec.system).render(exemplos=exemplos).strip()
        user = self._env.from_string(spec.user).render(**variaveis).strip()
        return PromptRenderizado(
            id=spec.id,
            versao=spec.versao,
            system=system,
            user=user,
            schema=spec.modelo_saida,
            effort=spec.effort,
            max_tokens=spec.max_tokens,
        )

    def _validar_variaveis(self, spec: PromptSpec) -> None:
        """Garante que o template usa exatamente as variáveis declaradas no YAML.

        O system prompt não recebe variáveis de requisição (só `exemplos`), o que o
        mantém estável entre chamadas e elegível para prompt caching.
        """
        usadas_user = jinja2.meta.find_undeclared_variables(self._env.parse(spec.user))
        usadas_system = jinja2.meta.find_undeclared_variables(self._env.parse(spec.system))
        if dinamicas := usadas_system - {"exemplos"}:
            raise ErroPrompt(f"{spec.id}: system usa variáveis de requisição {sorted(dinamicas)}")
        if usadas_user != set(spec.variaveis):
            raise ErroPrompt(
                f"{spec.id}: template user usa {sorted(usadas_user)}, "
                f"mas declara {sorted(spec.variaveis)}"
            )

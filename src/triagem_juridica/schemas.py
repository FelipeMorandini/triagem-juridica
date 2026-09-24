"""Contratos de dados do sistema.

Cada prompt da biblioteca declara um destes modelos como `schema_saida`. O modelo é
enviado à API como structured output, então a resposta do LLM chega já validada.
Isso elimina parsing frágil de texto e é a base da consistência dos outputs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerações de domínio
# ---------------------------------------------------------------------------


class TipoContato(StrEnum):
    NOVO_CASO = "novo_caso"
    CLIENTE_EXISTENTE = "cliente_existente"
    DUVIDA_GERAL = "duvida_geral"
    SPAM = "spam"
    FORA_DE_ESCOPO = "fora_de_escopo"


class AreaDireito(StrEnum):
    TRABALHISTA = "trabalhista"
    CONSUMIDOR = "consumidor"
    FAMILIA = "familia"
    PREVIDENCIARIO = "previdenciario"
    CIVEL = "civel"
    CRIMINAL = "criminal"
    TRIBUTARIO = "tributario"
    EMPRESARIAL = "empresarial"
    NAO_APLICAVEL = "nao_aplicavel"


class NivelUrgencia(StrEnum):
    CRITICA = "critica"
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"

    @property
    def peso(self) -> int:
        return {"critica": 3, "alta": 2, "media": 1, "baixa": 0}[self.value]


class StatusTriagem(StrEnum):
    PRONTA_PARA_ENVIO = "pronta_para_envio"
    REVISAO_HUMANA = "revisao_humana"
    ENCERRADA = "encerrada"


# ---------------------------------------------------------------------------
# Saídas de cada etapa (structured outputs)
# ---------------------------------------------------------------------------


class Classificacao(BaseModel):
    """Etapa 1: o que é este contato e de que área do direito ele trata."""

    tipo_contato: TipoContato
    area: AreaDireito
    confianca: float = Field(ge=0, le=1, description="Confiança entre 0 e 1.")
    justificativa: str = Field(
        description="Elementos da mensagem que sustentam a classificação (até 3 frases)."
    )


class DataRelevante(BaseModel):
    descricao: str = Field(description="O que aconteceu ou vai acontecer nesta data.")
    data: str | None = Field(
        description="Data no formato AAAA-MM-DD quando for possível determinar; senão null."
    )
    trecho: str = Field(description="Trecho literal da mensagem que comprova a data.")


class Extracao(BaseModel):
    """Etapa 2: fatos estruturados, sem inferência além do que está escrito."""

    nome_cliente: str | None
    telefone: str | None
    email: str | None
    resumo_fatos: str = Field(description="Resumo objetivo dos fatos, em terceira pessoa.")
    partes_contrarias: list[str]
    datas_relevantes: list[DataRelevante]
    documentos_mencionados: list[str]
    valores_mencionados: list[str]
    informacoes_faltantes: list[str] = Field(
        description="Informações essenciais para avaliar o caso que o cliente não forneceu."
    )


class AnaliseUrgencia(BaseModel):
    """Etapa 3: urgência e risco de perda de prazo."""

    nivel: NivelUrgencia
    prazo_identificado: str | None = Field(
        description="Prazo concreto identificado (ex.: 'audiência em 2026-10-02'), ou null."
    )
    fundamentacao: str = Field(
        description="Fatos e critério da rubrica que determinam o nível (até 4 frases)."
    )
    riscos: list[str]
    acao_recomendada: str = Field(description="Próximo passo interno para a equipe do escritório.")


class RespostaCliente(BaseModel):
    """Etapa 4: primeira resposta ao potencial cliente."""

    assunto: str
    corpo: str
    perguntas_ao_cliente: list[str] = Field(
        description="Perguntas para obter as informações faltantes."
    )


class Violacao(BaseModel):
    regra: str = Field(description="Código da regra violada, ex.: R1.")
    trecho: str = Field(description="Trecho da resposta que viola a regra.")
    correcao: str = Field(description="Como corrigir.")


class Revisao(BaseModel):
    """Etapa 5: LLM-as-judge verificando conformidade da resposta."""

    aprovado: bool
    violacoes: list[Violacao]
    parecer: str = Field(description="Resumo do resultado da revisão em uma ou duas frases.")


# ---------------------------------------------------------------------------
# Resultado final do pipeline
# ---------------------------------------------------------------------------


class FichaTriagem(BaseModel):
    """Ficha consolidada entregue à equipe do escritório."""

    recebido_em: datetime
    canal: str
    status: StatusTriagem
    classificacao: Classificacao | None = None
    extracao: Extracao | None = None
    urgencia: AnaliseUrgencia | None = None
    resposta: RespostaCliente | None = None
    revisao: Revisao | None = None
    tentativas_resposta: int = 0
    alertas: list[str] = Field(default_factory=list)
    versoes_prompts: dict[str, str] = Field(default_factory=dict)


SCHEMAS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (Classificacao, Extracao, AnaliseUrgencia, RespostaCliente, Revisao)
}

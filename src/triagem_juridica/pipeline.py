"""Pipeline multi-etapas de triagem.

    mensagem
       │  guardrail de entrada (determinístico)
       ▼
    [1] classificacao ──► spam / fora_de_escopo ──► ficha ENCERRADA (sem mais chamadas)
       │
       ▼
    [2] extracao ──► [3] urgencia
       │
       ▼
    [4] resposta ◄─────────────── feedback ──────────────┐
       │  guardrail de saída (determinístico) ── falhou ──┤
       ▼                                                  │
    [5] revisao (LLM-as-judge) ────────── reprovou ───────┘   (até N tentativas)
       │
       ▼
    ficha PRONTA_PARA_ENVIO ou REVISAO_HUMANA

Cada etapa é um prompt da biblioteca; o pipeline só orquestra, encadeia as saídas e
decide o roteamento. Falhas do modelo nunca derrubam a triagem: a ficha vai para
revisão humana com um alerta explicando o motivo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from triagem_juridica import guardrails
from triagem_juridica.llm import LLM, ErroLLM
from triagem_juridica.prompt_library import BibliotecaPrompts
from triagem_juridica.schemas import (
    AnaliseUrgencia,
    Classificacao,
    Extracao,
    FichaTriagem,
    NivelUrgencia,
    RespostaCliente,
    Revisao,
    StatusTriagem,
    TipoContato,
)

logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
TIPOS_ENCERRADOS = {TipoContato.SPAM, TipoContato.FORA_DE_ESCOPO}

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ConfigPipeline:
    nome_escritorio: str = "Morandini Advocacia"
    max_tentativas_resposta: int = 2
    limiar_confianca: float = 0.6
    # Casos críticos sempre passam por um advogado antes do envio, mesmo aprovados.
    critica_exige_humano: bool = True


class PipelineTriagem:
    def __init__(
        self,
        llm: LLM,
        biblioteca: BibliotecaPrompts | None = None,
        config: ConfigPipeline | None = None,
    ) -> None:
        self._llm = llm
        self._biblioteca = biblioteca or BibliotecaPrompts()
        self._config = config or ConfigPipeline()

    def processar(
        self,
        mensagem: str,
        canal: str = "email",
        recebido_em: datetime | None = None,
    ) -> FichaTriagem:
        recebido_em = recebido_em or datetime.now(FUSO)
        texto = guardrails.validar_entrada(mensagem)
        ficha = FichaTriagem(
            recebido_em=recebido_em, canal=canal, status=StatusTriagem.REVISAO_HUMANA
        )

        try:
            self._executar(ficha, texto)
        except ErroLLM as erro:
            logger.warning("triagem interrompida: %s", erro)
            ficha.status = StatusTriagem.REVISAO_HUMANA
            ficha.alertas.append(f"Falha na etapa de IA, triagem manual necessária: {erro}")
        return ficha

    # ------------------------------------------------------------------ etapas

    def _executar(self, ficha: FichaTriagem, texto: str) -> None:
        classificacao = self._chamar(
            ficha, Classificacao, "classificacao", canal=ficha.canal, mensagem=texto
        )
        ficha.classificacao = classificacao

        if classificacao.tipo_contato in TIPOS_ENCERRADOS:
            ficha.status = StatusTriagem.ENCERRADA
            return
        if classificacao.confianca < self._config.limiar_confianca:
            ficha.alertas.append(
                f"Classificação com baixa confiança ({classificacao.confianca:.2f}); conferir."
            )

        area = classificacao.area.value
        ficha.extracao = self._chamar(
            ficha,
            Extracao,
            "extracao",
            area=area,
            data_recebimento=ficha.recebido_em.date().isoformat(),
            mensagem=texto,
        )
        ficha.urgencia = self._chamar(
            ficha,
            AnaliseUrgencia,
            "urgencia",
            area=area,
            data_hoje=ficha.recebido_em.date().isoformat(),
            ficha=ficha.extracao.model_dump_json(indent=2),
            mensagem=texto,
        )
        if ficha.urgencia.nivel is NivelUrgencia.CRITICA and self._config.critica_exige_humano:
            ficha.alertas.append(
                "Urgência CRÍTICA: advogado deve revisar e contatar o cliente hoje."
            )

        aprovada = self._gerar_resposta_revisada(ficha)
        ficha.status = (
            StatusTriagem.PRONTA_PARA_ENVIO
            if aprovada and not ficha.alertas
            else StatusTriagem.REVISAO_HUMANA
        )

    def _gerar_resposta_revisada(self, ficha: FichaTriagem) -> bool:
        """Ciclo gerar → verificar → revisar, com feedback entre tentativas."""
        assert ficha.classificacao and ficha.extracao and ficha.urgencia
        ficha_json = ficha.extracao.model_dump_json(indent=2)
        urgencia_json = ficha.urgencia.model_dump_json(include={"nivel", "prazo_identificado"})
        feedback = ""

        for tentativa in range(1, self._config.max_tentativas_resposta + 1):
            ficha.tentativas_resposta = tentativa
            resposta = self._chamar(
                ficha,
                RespostaCliente,
                "resposta",
                canal=ficha.canal,
                classificacao=ficha.classificacao.model_dump_json(include={"tipo_contato", "area"}),
                feedback_revisao=feedback,
                ficha=ficha_json,
                nome_escritorio=self._config.nome_escritorio,
                urgencia=urgencia_json,
            )
            ficha.resposta = resposta

            violacoes = guardrails.verificar_resposta(resposta, ficha.canal)
            if violacoes:
                # Reprovada sem gastar a chamada de revisão.
                ficha.revisao = None
                feedback = "\n".join(f"- {v}" for v in violacoes)
                logger.info("tentativa %d reprovada no guardrail: %s", tentativa, violacoes)
                continue

            revisao = self._chamar(
                ficha,
                Revisao,
                "revisao",
                canal=ficha.canal,
                ficha=ficha_json,
                resposta=guardrails.texto_enviado(resposta, ficha.canal),
                urgencia=urgencia_json,
            )
            ficha.revisao = revisao
            if revisao.aprovado and not revisao.violacoes:
                return True
            feedback = (
                "\n".join(f'- {v.regra}: "{v.trecho}" → {v.correcao}' for v in revisao.violacoes)
                or "- A revisão reprovou a resposta sem detalhar; reescreva seguindo as regras."
            )
            logger.info("tentativa %d reprovada na revisão", tentativa)

        ficha.alertas.append(
            f"Resposta não aprovada após {self._config.max_tentativas_resposta} tentativas; "
            f"último feedback:\n{feedback}"
        )
        return False

    def _chamar(
        self, triagem: FichaTriagem, tipo: type[T], prompt_id: str, /, **variaveis: str
    ) -> T:
        # Parâmetros posicionais: `ficha` também é nome de variável de prompt.
        prompt = self._biblioteca.renderizar(prompt_id, **variaveis)
        triagem.versoes_prompts[prompt.id] = prompt.versao
        saida = self._llm.gerar(prompt)
        if not isinstance(saida, tipo):
            raise ErroLLM(f"{prompt_id}: esperado {tipo.__name__}, recebido {type(saida).__name__}")
        return saida

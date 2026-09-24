"""Guardrails determinísticos.

Verificações baratas, sem LLM, que rodam antes e depois das chamadas ao modelo. Elas
não substituem a revisão por LLM (que entende contexto); elas pegam os erros mais graves
com certeza absoluta e custo zero, e evitam gastar uma chamada de revisão com um texto
que já sabemos estar reprovado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from triagem_juridica.schemas import RespostaCliente

MAX_CARACTERES_ENTRADA = 8_000

# O prompt de resposta pede até 180 palavras (e-mail) e 90 (WhatsApp). Modelos não contam
# palavras com precisão, então a checagem é feita aqui, com código, e com uma folga.
LIMITE_PALAVRAS = {"whatsapp": 110}
LIMITE_PALAVRAS_PADRAO = 220


class EntradaInvalida(ValueError):
    pass


def validar_entrada(mensagem: str) -> str:
    texto = mensagem.strip()
    if not texto:
        raise EntradaInvalida("mensagem vazia")
    if len(texto) > MAX_CARACTERES_ENTRADA:
        raise EntradaInvalida(
            f"mensagem com {len(texto)} caracteres excede o limite de {MAX_CARACTERES_ENTRADA}"
        )
    return texto


@dataclass(frozen=True)
class RegraTexto:
    codigo: str
    descricao: str
    padrao: re.Pattern[str]
    # Se o trecho logo antes do achado casar com este padrão, não é violação
    # (ex.: "não envie seus dados bancários" é um aviso, não um pedido).
    excecao_antes: re.Pattern[str] | None = None

    def buscar(self, texto: str) -> re.Match[str] | None:
        for achado in self.padrao.finditer(texto):
            antes = texto[max(0, achado.start() - 60) : achado.start()]
            if self.excecao_antes is None or not self.excecao_antes.search(antes):
                return achado
        return None


# Valores só violam R2 quando ligados a honorários, custas ou ao que o cliente vai
# receber. Repetir o valor que o próprio cliente citou ("dívida de R$ 890") é permitido.
_CONTEXTO_R2 = r"(honorári\w*|custa\w*|consulta|indeniza\w*|receber|cobramos|investimento)"


REGRAS_RESPOSTA: tuple[RegraTexto, ...] = (
    RegraTexto(
        "R1",
        "promessa de resultado",
        re.compile(
            r"causa ganha|caso ganho|praticamente ganh|com certeza (vai|vamos|você)"
            r"|garant\w* (a |o )?(vitória|êxito|sucesso|resultado|indenização)"
            r"|\d{1,3} ?% de chance",
            re.IGNORECASE,
        ),
    ),
    RegraTexto(
        "R2",
        "valor monetário (honorários, custas ou indenização)",
        re.compile(
            rf"{_CONTEXTO_R2}[^.\n]{{0,60}}(R\$ ?\d|\d+ ?reais)"
            rf"|(R\$ ?\d|\d+ ?reais)[^.\n]{{0,60}}{_CONTEXTO_R2}"
            r"|honorários (de|a partir|começam)",
            re.IGNORECASE,
        ),
    ),
    RegraTexto(
        "R5",
        "pedido de dado sensível",
        re.compile(
            r"\bsenha\b|número do cartão|dados bancários|agência e conta|código de segurança",
            re.IGNORECASE,
        ),
        excecao_antes=re.compile(r"\b(não|nunca|jamais)\b[^.\n]*$", re.IGNORECASE),
    ),
)


def texto_enviado(resposta: RespostaCliente, canal: str) -> str:
    """O que o cliente de fato recebe: no WhatsApp o assunto é só registro interno."""
    if canal == "whatsapp":
        return resposta.corpo
    return f"Assunto: {resposta.assunto}\n\n{resposta.corpo}"


def verificar_resposta(resposta: RespostaCliente, canal: str = "email") -> list[str]:
    """Retorna as violações encontradas, no formato 'R1: promessa de resultado ("trecho")'."""
    texto = texto_enviado(resposta, canal)
    violacoes = []
    for regra in REGRAS_RESPOSTA:
        if achado := regra.buscar(texto):
            violacoes.append(f'{regra.codigo}: {regra.descricao} ("{achado.group(0)}")')

    limite = LIMITE_PALAVRAS.get(canal, LIMITE_PALAVRAS_PADRAO)
    if (palavras := len(resposta.corpo.split())) > limite:
        violacoes.append(f"F2: corpo com {palavras} palavras excede o limite do canal {canal}")
    return violacoes

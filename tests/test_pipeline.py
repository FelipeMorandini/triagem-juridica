import pytest

from triagem_juridica.guardrails import EntradaInvalida
from triagem_juridica.llm import ErroLLM, LLMFalso, RecusaDoModelo
from triagem_juridica.pipeline import ConfigPipeline, PipelineTriagem
from triagem_juridica.schemas import StatusTriagem

from .conftest import (
    MENSAGEM_TRABALHISTA,
    RECEBIDO_EM,
    classificacao,
    extracao,
    resposta,
    revisao_aprovada,
    revisao_reprovada,
    roteiro_feliz,
    urgencia,
)

ORDEM_COMPLETA = ["classificacao", "extracao", "urgencia", "resposta", "revisao"]


def _processar(roteiro, config=None, mensagem=MENSAGEM_TRABALHISTA, canal="email"):
    llm = LLMFalso(roteiro)
    pipeline = PipelineTriagem(llm, config=config)
    ficha = pipeline.processar(mensagem, canal=canal, recebido_em=RECEBIDO_EM)
    return ficha, llm


def test_fluxo_completo_gera_ficha_pronta_para_envio(biblioteca):
    ficha, llm = _processar(roteiro_feliz())

    assert llm.ids_chamados() == ORDEM_COMPLETA
    assert ficha.status is StatusTriagem.PRONTA_PARA_ENVIO
    assert ficha.alertas == []
    assert ficha.tentativas_resposta == 1
    assert ficha.resposta.assunto.startswith("Recebemos")
    assert ficha.versoes_prompts == {e: biblioteca.get(e).versao for e in ORDEM_COMPLETA}


@pytest.mark.parametrize("tipo", ["spam", "fora_de_escopo"])
def test_contato_sem_caso_encerra_apos_classificacao(tipo):
    ficha, llm = _processar(
        {"classificacao": [classificacao(tipo_contato=tipo, area="nao_aplicavel")]}
    )
    assert llm.ids_chamados() == ["classificacao"]
    assert ficha.status is StatusTriagem.ENCERRADA
    assert ficha.resposta is None


def test_saida_de_cada_etapa_alimenta_a_seguinte():
    _, llm = _processar(roteiro_feliz(), config=ConfigPipeline(nome_escritorio="Silva & Lima"))
    prompts = {chamada.id: chamada for chamada in llm.chamadas}

    assert "trabalhista" in prompts["extracao"].user
    assert "2026-09-23" in prompts["extracao"].user
    assert "A cliente foi demitida sem justa causa" in prompts["urgencia"].user
    assert "Silva & Lima" in prompts["resposta"].user
    assert '"nivel":"media"' in prompts["resposta"].user
    assert "Um advogado da nossa equipe" in prompts["revisao"].user


def test_resposta_so_recebe_dados_internos_necessarios():
    """A justificativa da classificação e a fundamentação da urgência não vão para a resposta."""
    _, llm = _processar(roteiro_feliz())
    usuario = next(c for c in llm.chamadas if c.id == "resposta").user
    assert "Relata demissão" not in usuario
    assert "Demissão há 22 dias" not in usuario


def test_baixa_confianca_segue_mas_exige_humano():
    ficha, llm = _processar(roteiro_feliz(classificacao=[classificacao(confianca=0.4)]))
    assert llm.ids_chamados() == ORDEM_COMPLETA
    assert ficha.status is StatusTriagem.REVISAO_HUMANA
    assert "baixa confiança" in ficha.alertas[0]


def test_urgencia_critica_exige_humano_mesmo_aprovada():
    ficha, _ = _processar(roteiro_feliz(urgencia=[urgencia(nivel="critica")]))
    assert ficha.revisao.aprovado
    assert ficha.status is StatusTriagem.REVISAO_HUMANA
    assert any("CRÍTICA" in alerta for alerta in ficha.alertas)


def test_urgencia_critica_pode_dispensar_humano_por_configuracao():
    ficha, _ = _processar(
        roteiro_feliz(urgencia=[urgencia(nivel="critica")]),
        config=ConfigPipeline(critica_exige_humano=False),
    )
    assert ficha.status is StatusTriagem.PRONTA_PARA_ENVIO


def test_guardrail_reprova_sem_gastar_revisao_e_devolve_feedback():
    ruim = resposta(corpo="Olá, Ana! Seu caso é praticamente ganho. Equipe")
    ficha, llm = _processar(roteiro_feliz(resposta=[ruim, resposta()]))

    assert llm.ids_chamados() == [
        "classificacao",
        "extracao",
        "urgencia",
        "resposta",
        "resposta",
        "revisao",
    ]
    segunda = [c for c in llm.chamadas if c.id == "resposta"][1]
    assert "<feedback_revisao>" in segunda.user
    assert "R1" in segunda.user
    assert ficha.status is StatusTriagem.PRONTA_PARA_ENVIO
    assert ficha.tentativas_resposta == 2


def test_revisao_reprovada_gera_nova_tentativa_com_correcao():
    ficha, llm = _processar(
        roteiro_feliz(
            resposta=[resposta(), resposta()],
            revisao=[revisao_reprovada(trecho="você tem direito a"), revisao_aprovada()],
        )
    )
    segunda = [c for c in llm.chamadas if c.id == "resposta"][1]
    assert "você tem direito a" in segunda.user
    assert ficha.status is StatusTriagem.PRONTA_PARA_ENVIO
    assert ficha.tentativas_resposta == 2


def test_esgotar_tentativas_envia_para_humano():
    ficha, llm = _processar(
        roteiro_feliz(
            resposta=[resposta(), resposta(), resposta()],
            revisao=[revisao_reprovada()] * 3,
        ),
        config=ConfigPipeline(max_tentativas_resposta=3),
    )
    assert llm.ids_chamados().count("resposta") == 3
    assert ficha.status is StatusTriagem.REVISAO_HUMANA
    assert "após 3 tentativas" in ficha.alertas[-1]
    assert ficha.resposta is not None  # rascunho fica disponível para o advogado


def test_revisao_incoerente_nao_e_aceita():
    """aprovado=true com violações listadas é tratado como reprovação."""
    incoerente = revisao_aprovada() | {"violacoes": revisao_reprovada()["violacoes"]}
    ficha, _ = _processar(
        roteiro_feliz(resposta=[resposta(), resposta()], revisao=[incoerente, revisao_aprovada()])
    )
    assert ficha.tentativas_resposta == 2


@pytest.mark.parametrize("etapa", ["extracao", "urgencia", "resposta", "revisao"])
def test_falha_do_modelo_nao_derruba_a_triagem(etapa):
    roteiro = roteiro_feliz(**{etapa: [RecusaDoModelo(f"{etapa}: recusou")]})
    ficha, _ = _processar(roteiro)
    assert ficha.status is StatusTriagem.REVISAO_HUMANA
    assert ficha.classificacao is not None
    assert "recusou" in ficha.alertas[-1]


def test_falha_na_classificacao_preserva_mensagem_para_triagem_manual():
    ficha, _ = _processar({"classificacao": [ErroLLM("timeout")]})
    assert ficha.status is StatusTriagem.REVISAO_HUMANA
    assert ficha.classificacao is None


def test_entrada_vazia_e_rejeitada_antes_de_chamar_o_modelo():
    llm = LLMFalso({})
    with pytest.raises(EntradaInvalida):
        PipelineTriagem(llm).processar("   ")
    assert llm.chamadas == []


def test_canal_e_repassado_aos_prompts():
    ficha, llm = _processar(roteiro_feliz(), canal="whatsapp")
    assert ficha.canal == "whatsapp"
    assert 'canal "whatsapp"' in llm.chamadas[0].user
    revisao = next(c for c in llm.chamadas if c.id == "revisao")
    assert "Assunto:" not in revisao.user  # no WhatsApp o assunto não é enviado


def test_extracao_sem_dados_opcionais_e_aceita():
    vazia = extracao(nome_cliente=None, datas_relevantes=[], partes_contrarias=[])
    ficha, _ = _processar(roteiro_feliz(extracao=[vazia]))
    assert ficha.status is StatusTriagem.PRONTA_PARA_ENVIO

"""Uso do sistema como biblioteca, com a API real.

export ANTHROPIC_API_KEY=...
uv run python examples/uso_programatico.py
"""

from pathlib import Path

from triagem_juridica import AnthropicLLM, BibliotecaPrompts, ConfigPipeline, PipelineTriagem

biblioteca = BibliotecaPrompts()

# 1) Um prompt isolado: a biblioteca serve para qualquer fluxo, não só para o pipeline.
prompt = biblioteca.renderizar(
    "classificacao",
    canal="whatsapp",
    mensagem="Oi, meu voo foi cancelado e a companhia não quer reembolsar.",
)
llm = AnthropicLLM()
classificacao = llm.gerar(prompt)
print(f"[{prompt.id} v{prompt.versao}] {classificacao.tipo_contato} / {classificacao.area}")

# 2) O pipeline completo.
pipeline = PipelineTriagem(llm, config=ConfigPipeline(nome_escritorio="Silva & Lima Advogados"))
for arquivo in sorted((Path(__file__).parent / "mensagens").glob("*.txt")):
    canal = "whatsapp" if "whatsapp" in arquivo.stem else "email"
    ficha = pipeline.processar(arquivo.read_text("utf-8"), canal=canal)
    urgencia = ficha.urgencia.nivel if ficha.urgencia else "-"
    print(f"\n== {arquivo.name}: {ficha.status} (urgência: {urgencia})")
    for alerta in ficha.alertas:
        print(f"   ! {alerta}")
    if ficha.resposta:
        print(ficha.resposta.corpo)

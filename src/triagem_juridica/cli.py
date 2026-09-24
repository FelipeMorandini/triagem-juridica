"""Interface de linha de comando.

triagem prompts                          # lista a biblioteca
triagem renderizar classificacao --var canal=email --var mensagem="..."
triagem processar mensagem.txt --canal whatsapp
cat mensagem.txt | triagem processar -
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from triagem_juridica.llm import AnthropicLLM
from triagem_juridica.pipeline import ConfigPipeline, PipelineTriagem
from triagem_juridica.prompt_library import BibliotecaPrompts


def _listar(_: argparse.Namespace) -> int:
    biblioteca = BibliotecaPrompts()
    for prompt_id in biblioteca.ids():
        spec = biblioteca.get(prompt_id)
        print(f"{spec.id} v{spec.versao}  [{spec.schema_saida}, effort={spec.effort}]")
        print(f"    {spec.descricao.strip()}")
        print(f"    técnicas: {', '.join(spec.tecnicas)}\n")
    return 0


def _renderizar(args: argparse.Namespace) -> int:
    variaveis = dict(par.split("=", 1) for par in args.var)
    prompt = BibliotecaPrompts().renderizar(args.prompt_id, **variaveis)
    print(f"===== SYSTEM ({prompt.id} v{prompt.versao}) =====\n{prompt.system}\n")
    print(f"===== USER =====\n{prompt.user}")
    return 0


def _processar(args: argparse.Namespace) -> int:
    mensagem = sys.stdin.read() if args.arquivo == "-" else Path(args.arquivo).read_text("utf-8")
    pipeline = PipelineTriagem(
        AnthropicLLM(modelo=args.modelo),
        config=ConfigPipeline(nome_escritorio=args.escritorio),
    )
    ficha = pipeline.processar(mensagem, canal=args.canal)
    saida = ficha.model_dump_json(indent=2)
    if args.saida:
        Path(args.saida).write_text(saida + "\n", encoding="utf-8")
        print(f"ficha salva em {args.saida} (status: {ficha.status})")
    else:
        print(saida)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triagem", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="loga cada chamada ao modelo")
    sub = parser.add_subparsers(required=True)

    p_listar = sub.add_parser("prompts", help="lista os prompts da biblioteca")
    p_listar.set_defaults(func=_listar)

    p_render = sub.add_parser("renderizar", help="mostra um prompt renderizado, sem chamar a API")
    p_render.add_argument("prompt_id")
    p_render.add_argument("--var", action="append", default=[], metavar="NOME=VALOR")
    p_render.set_defaults(func=_renderizar)

    p_proc = sub.add_parser("processar", help="executa o pipeline completo em uma mensagem")
    p_proc.add_argument("arquivo", help="arquivo com a mensagem, ou - para stdin")
    p_proc.add_argument("--canal", default="email", choices=["email", "whatsapp", "formulario"])
    p_proc.add_argument("--escritorio", default=ConfigPipeline.nome_escritorio)
    p_proc.add_argument("--modelo", default=None, help="sobrescreve TRIAGEM_MODELO")
    p_proc.add_argument("--saida", help="grava a ficha JSON neste arquivo")
    p_proc.set_defaults(func=_processar)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

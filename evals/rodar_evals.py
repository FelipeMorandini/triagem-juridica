"""Avaliação do pipeline contra a API real.

    uv run python evals/rodar_evals.py                  # todos os casos, 1 execução
    uv run python evals/rodar_evals.py --repeticoes 3   # mede consistência
    uv run python evals/rodar_evals.py --casos criminal_prisao spam_injecao

Métricas:
  - acurácia de tipo_contato e área
  - urgência segura: nível obtido >= urgencia_minima
  - conformidade: toda resposta gerada passa nos guardrails e na revisão
  - consistência: casos em que todas as repetições deram o mesmo tipo, área e urgência

Custo: cada caso completo faz de 4 a 7 chamadas ao modelo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from triagem_juridica import AnthropicLLM, PipelineTriagem
from triagem_juridica.guardrails import verificar_resposta
from triagem_juridica.schemas import FichaTriagem, NivelUrgencia

RAIZ = Path(__file__).parent


def carregar_casos(ids: list[str] | None = None, apenas_fumaca: bool = False) -> list[dict]:
    casos = yaml.safe_load((RAIZ / "casos.yaml").read_text("utf-8"))
    if ids:
        casos = [c for c in casos if c["id"] in ids]
    if apenas_fumaca:
        casos = [c for c in casos if c.get("fumaca")]
    return casos


def avaliar_ficha(caso: dict, ficha: FichaTriagem) -> dict[str, bool | None]:
    """Compara uma ficha com o esperado. None = critério não se aplica."""
    esperado = caso["esperado"]
    c, u = ficha.classificacao, ficha.urgencia
    areas_aceitas = caso.get("areas_aceitas") or ([esperado["area"]] if "area" in esperado else [])

    resultado: dict[str, bool | None] = {
        "tipo": c is not None and c.tipo_contato == esperado["tipo_contato"],
        "area": (c is not None and c.area in areas_aceitas) if areas_aceitas else None,
        "urgencia": None,
        "conformidade": None,
    }
    if "urgencia_minima" in esperado:
        minimo = NivelUrgencia(esperado["urgencia_minima"])
        resultado["urgencia"] = u is not None and u.nivel.peso >= minimo.peso
    if ficha.resposta is not None:
        resultado["conformidade"] = (
            not verificar_resposta(ficha.resposta, ficha.canal)
            and ficha.revisao is not None
            and ficha.revisao.aprovado
        )
    return resultado


def assinatura(ficha: FichaTriagem) -> tuple:
    c, u = ficha.classificacao, ficha.urgencia
    return (
        c and c.tipo_contato.value,
        c and c.area.value,
        u and u.nivel.value,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeticoes", type=int, default=1)
    parser.add_argument("--casos", nargs="*")
    parser.add_argument("--modelo")
    args = parser.parse_args()

    pipeline = PipelineTriagem(AnthropicLLM(modelo=args.modelo))
    casos = carregar_casos(args.casos)
    totais: Counter = Counter()
    acertos: Counter = Counter()
    consistentes = 0
    relatorio = []

    for caso in casos:
        execucoes = []
        assinaturas = set()
        for _ in range(args.repeticoes):
            ficha = pipeline.processar(caso["mensagem"], canal=caso["canal"])
            notas = avaliar_ficha(caso, ficha)
            for metrica, ok in notas.items():
                if ok is not None:
                    totais[metrica] += 1
                    acertos[metrica] += ok
            assinaturas.add(assinatura(ficha))
            execucoes.append({"notas": notas, "ficha": json.loads(ficha.model_dump_json())})

        consistentes += len(assinaturas) == 1
        falhas = sorted({m for e in execucoes for m, ok in e["notas"].items() if ok is False})
        print(f"{'OK ' if not falhas else 'ERR'} {caso['id']:<28} {' '.join(falhas)}")
        relatorio.append({"caso": caso["id"], "execucoes": execucoes})

    print("\nMétrica        acertos")
    for metrica in ("tipo", "area", "urgencia", "conformidade"):
        if totais[metrica]:
            taxa = acertos[metrica] / totais[metrica]
            print(f"{metrica:<14} {acertos[metrica]}/{totais[metrica]} ({taxa:.0%})")
    if args.repeticoes > 1:
        print(f"{'consistência':<14} {consistentes}/{len(casos)} ({consistentes / len(casos):.0%})")

    destino = RAIZ / "resultados"
    destino.mkdir(exist_ok=True)
    arquivo = destino / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    arquivo.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nrelatório completo: {arquivo.relative_to(RAIZ.parent)}")
    return 0 if all(acertos[m] == totais[m] for m in totais) else 1


if __name__ == "__main__":
    sys.exit(main())

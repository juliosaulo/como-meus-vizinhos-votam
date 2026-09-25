"""Orquestrador — roda o pipeline inteiro, na ordem, com um comando.

    python rodar_pipeline.py                 # tudo
    python rodar_pipeline.py --de 31         # a partir de um passo
    python rodar_pipeline.py --so 21 22      # só alguns passos
    python rodar_pipeline.py --listar

Os passos são independentes e guardam resultado em disco, então interromper e
retomar é seguro. O passo 31 (CNEFE) tem checkpoint por UF: reexecutar pula as
UFs já processadas.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

PASSOS = [
    ("11", "pipeline/11_montar_dim_regiao.py", "dimensão de regiões"),
    ("21", "pipeline/21_processar_votacao.py", "votação por local de votação"),
    ("22", "pipeline/22_votos_por_regiao.py", "votos por região"),
    ("31", "pipeline/31_atribuir_endereco_regiao.py", "endereço → região"),
    ("32", "pipeline/32_montar_indice_ruas.py", "índice de ruas"),
    ("40", "pipeline/40_build_dados_publicados.py", "dados publicados"),
    ("pub", "qualidade/validar_publicado.py", "validação dos arquivos publicados"),
    ("qa", "qualidade/validar_indice_ruas.py", "validação ponta a ponta"),
    ("cob", "qualidade/relatorio_cobertura.py", "relatório de cobertura"),
    ("dist", "qualidade/diagnostico_distancias.py", "diagnóstico de distâncias"),
]


def rodar(script: str) -> None:
    resultado = subprocess.run([sys.executable, script], cwd=BASE_DIR)
    if resultado.returncode != 0:
        raise SystemExit(f"\nFALHOU: {script} (código {resultado.returncode})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--de", help="começa neste passo")
    parser.add_argument("--so", nargs="+", help="roda só estes passos")
    parser.add_argument("--listar", action="store_true")
    args = parser.parse_args()

    if args.listar:
        for codigo, script, descricao in PASSOS:
            print(f"  {codigo:>3}  {descricao:<32} {script}")
        return

    escolhidos = PASSOS
    if args.so:
        escolhidos = [p for p in PASSOS if p[0] in set(args.so)]
    elif args.de:
        indices = [i for i, p in enumerate(PASSOS) if p[0] == args.de]
        if not indices:
            raise SystemExit(f"passo desconhecido: {args.de}")
        escolhidos = PASSOS[indices[0]:]

    inicio = time.time()
    for codigo, script, descricao in escolhidos:
        print(f"\n{'=' * 70}\n  passo {codigo} — {descricao}\n{'=' * 70}")
        rodar(script)

    print(f"\nconcluído em {time.time() - inicio:.0f}s")


if __name__ == "__main__":
    main()

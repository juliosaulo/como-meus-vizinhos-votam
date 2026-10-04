"""A noite de domingo num comando só.

Encadeia coleta, conversão, conferência, troca do ano de referência e pipeline.
**Não publica**: para antes do deploy, de propósito, e imprime o comando a dar
depois de alguém olhar o relatório.

    python -u coleta_2026/domingo.py

    # destacado, para sobreviver ao fechamento do terminal (PowerShell):
    Start-Process python -ArgumentList "-u","coleta_2026/domingo.py" `
        -RedirectStandardOutput domingo.log -RedirectStandardError domingo.err -NoNewWindow

    python -u coleta_2026/domingo.py --de pipeline     # retoma de uma etapa

Por que existe
--------------
Cada etapa já é um comando que funciona sozinho. O problema é a madrugada: entre
o fim da coleta (~00:40) e o início do pipeline não pode haver ninguém esperando
para digitar a etapa seguinte. Este script é a espera automatizada.

A trava
-------
A conferência (seção 8 do README) roda **antes** de qualquer coisa que mude o
estado do projeto. Se ela acusar divergência, o script para ali: o
`ANO_REFERENCIA_MALHA` não é tocado, o pipeline não roda e nada é publicado. É
melhor acordar com o site do jeito que estava do que com ele errado.

Retomar é seguro
----------------
Toda etapa pode rodar de novo sem estragar o que já foi feito: a coleta retoma
pelo zip, a conversão substitui as linhas do ano, a conferência só lê, a troca do
ano é idempotente e o pipeline refaz do passo 22 em diante.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import tse

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config.py"
BASE_DE_VOTOS = RAIZ / "dados" / "intermediario" / "votos_local_votacao.parquet"

ETAPAS = ["coleta", "conversao", "conferencia", "referencia", "pipeline"]

# Os cargos que o projeto publica, e em que nível cada um tem arquivo oficial
# para comparar. Proporcional não tem agregado nacional útil: compara-se por UF.
CONFERENCIAS = [(1, "br"), (6, "uf")]


def agora() -> str:
    return time.strftime("%H:%M:%S")


def parar(mensagem: str) -> None:
    print(f"\n{'!' * 70}\n  PAROU: {mensagem}")
    print(f"  nada foi publicado. {agora()}\n{'!' * 70}")
    raise SystemExit(1)


def rodar(descricao: str, argumentos: list[str]) -> float:
    """Roda um script do projeto, deixando a saída dele aparecer no nosso log."""
    print(f"\n{'=' * 70}\n  {descricao} — {agora()}\n{'=' * 70}", flush=True)
    inicio = time.monotonic()
    resultado = subprocess.run([sys.executable, "-u", *argumentos], cwd=RAIZ,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    minutos = (time.monotonic() - inicio) / 60
    if resultado.returncode != 0:
        parar(f"{descricao} saiu com código {resultado.returncode} "
              f"depois de {minutos:.1f} min")
    print(f"\n  ok: {descricao} em {minutos:.1f} min", flush=True)
    return minutos


def conferir_base(ano: int) -> None:
    """A conversão mesclou mesmo o ano na tabela que o pipeline lê?

    Vale a conferência boba: se a mesclagem não aconteceu, o pipeline rodaria
    três horas com os dados de 2022 e publicaria uma eleição velha como nova.
    """
    import pandas as pd

    if not BASE_DE_VOTOS.exists():
        parar(f"{BASE_DE_VOTOS} não existe — a conversão não gravou a base do pipeline")
    base = pd.read_parquet(BASE_DE_VOTOS, columns=["ano_eleicao", "qt_votos"])
    por_ano = base.groupby("ano_eleicao")["qt_votos"].sum()
    print("\n  votos na tabela do pipeline, por ano:")
    for a, votos in por_ano.items():
        print(f"    {int(a)}: {int(votos):,}")
    if ano not in por_ano.index or por_ano.get(ano, 0) == 0:
        parar(f"não há voto de {ano} em {BASE_DE_VOTOS.name}")


def trocar_ano_de_referencia(ano: int) -> None:
    """Ajusta a única linha de configuração que muda no dia.

    Ela define quais regiões estão ativas; sem a troca, os locais que só existem
    em 2026 ficariam inativos e os votos deles iriam para o vizinho mais próximo.
    """
    texto = CONFIG.read_text(encoding="utf-8")
    achados = re.findall(r"^ANO_REFERENCIA_MALHA\s*=\s*(\d+)\s*$", texto, flags=re.M)
    if len(achados) != 1:
        parar(f"esperava uma linha de ANO_REFERENCIA_MALHA em {CONFIG.name}, "
              f"achei {len(achados)} — não vou editar no escuro")
    if int(achados[0]) == ano:
        print(f"  ANO_REFERENCIA_MALHA já é {ano}")
        return
    novo = re.sub(r"^ANO_REFERENCIA_MALHA\s*=\s*\d+\s*$", f"ANO_REFERENCIA_MALHA = {ano}",
                  texto, count=1, flags=re.M)
    CONFIG.write_text(novo, encoding="utf-8")
    print(f"  ANO_REFERENCIA_MALHA: {achados[0]} → {ano}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ano", type=int, default=2026)
    parser.add_argument("--turno", type=int, default=1)
    parser.add_argument("--de", choices=ETAPAS, default="coleta",
                        help="retoma desta etapa")
    parser.add_argument("--passadas", type=int, default=8)
    parser.add_argument("--espera", type=int, default=20)
    parser.add_argument("--eleicao", help="código da eleição; o padrão é descobrir no TSE")
    parser.add_argument("--uf", nargs="+",
                        help="só estas UFs, para ensaiar a cadeia inteira em miniatura")
    parser.add_argument("--com-malha", action="store_true",
                        help="refaz também os passos 31 e 32 (só se a malha mudou)")
    args = parser.parse_args()
    recorte = ["--uf", *args.uf] if args.uf else []

    inicio = time.monotonic()
    etapas = ETAPAS[ETAPAS.index(args.de):]
    print(f"=== domingo — {args.ano}, {args.turno}º turno | início {agora()}")
    print(f"    etapas: {', '.join(etapas)}")

    # O código da eleição não é fixo no projeto: sai do arquivo de configuração do
    # TSE. Se nem isso responder, não há motivo para começar as outras etapas.
    codigo = args.eleicao
    if codigo is None:
        eleicao = tse.Cliente().eleicao_federal(args.ano, args.turno)
        codigo = eleicao.codigo
        print(f"    eleição {codigo} — {eleicao.nome} (pleito {eleicao.pleito})")

    tempos: dict[str, float] = {}

    if "coleta" in etapas:
        tempos["coleta"] = rodar("coleta dos boletins", [
            "coleta_2026/coletar.py", "--ano", str(args.ano), "--turno", str(args.turno),
            "--passadas", str(args.passadas), "--espera", str(args.espera), *recorte])

    if "conversao" in etapas:
        tempos["conversão"] = rodar("conversão para a tabela do pipeline", [
            "coleta_2026/converter.py", "--ano", str(args.ano), "--turno", str(args.turno),
            "--eleicao", codigo, *recorte])
        conferir_base(args.ano)

    if "conferencia" in etapas:
        for cargo, nivel in CONFERENCIAS:
            tempos[f"conferência cargo {cargo}"] = rodar(
                f"conferência contra o TSE — cargo {cargo}, nível {nivel}", [
                    "coleta_2026/conferir.py", "--eleicao", codigo, "--ano", str(args.ano),
                    "--turno", str(args.turno), "--cargo", str(cargo), "--nivel", nivel,
                    "--resumido"])
        print("\n  as duas conferências passaram — autorizado a seguir")

    if "referencia" in etapas:
        print(f"\n{'=' * 70}\n  ano de referência da malha — {agora()}\n{'=' * 70}")
        trocar_ano_de_referencia(args.ano)

    if "pipeline" in etapas:
        # Com a malha de 2026 já publicada no sábado, os passos 31 e 32 não têm o
        # que refazer: a atribuição de endereço depende da malha, não do voto.
        # São 50 minutos a menos na madrugada. `--com-malha` refaz tudo, para o
        # caso de a malha ter mudado.
        passos = (["--de", "22"] if args.com_malha
                  else ["--so", "22", "40", "pub", "qa", "cob", "dist", "coord"])
        tempos["pipeline"] = rodar(
            "pipeline" + ("" if args.com_malha else " (sem refazer a malha)"),
            ["rodar_pipeline.py", *passos])

    print(f"\n{'=' * 70}\n  PRONTO PARA PUBLICAR — {agora()}\n{'=' * 70}")
    for etapa, minutos in tempos.items():
        print(f"    {etapa:<32} {minutos:>6.1f} min")
    print(f"    {'total':<32} {(time.monotonic() - inicio) / 60:>6.1f} min")
    print("\n  Nada foi publicado. Antes do deploy, vale olhar:")
    print("    - publicado/cobertura.json e as validações do pipeline, acima")
    print("    - o site local, com o servidor de teste, no desktop e no celular")
    print("\n  E então:")
    print("    python site/deploy/deploy.py --dry-run     # lista o que subiria")
    print("    python site/deploy/deploy.py")


if __name__ == "__main__":
    main()

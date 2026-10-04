"""Testa a camada ao vivo do site, inclusive buscando no TSE de verdade.

    python coleta_2026/teste_ao_vivo_js.py --zip dados/bruto/bu/RR.zip

Duas camadas, as duas em `site/dev/teste_ao_vivo.html`:

1. **A aritmética**, com boletins fabricados em JavaScript: a soma, as guardas e
   a forma de saída.
2. **A busca de verdade**, contra o servidor do TSE, com a eleição **de 2024** —
   real e encerrada, o que permite ver a camada funcionando com dado de verdade
   antes do dia da apuração. O valor esperado é calculado aqui, pelo parser em
   Python, a partir dos mesmos boletins já baixados.

O que este script prepara:

- `dados/teste/ao_vivo_2024/` — os estáticos do ensaio, gerados por `ao_vivo.py`;
- `dados/teste/ao_vivo_esperado.json` — para cada região escolhida, quantas urnas,
  o comparecimento e os votos por votável, segundo o Python.

E a regra do valor esperado é a mesma que o JavaScript aplica: **o boletim manda
sobre o nosso mapa** — só entra urna cujo local declarado pertence à região.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bu  # noqa: E402
import teste_bu_js  # noqa: E402  (reaproveita o servidor e o playwright)

DESTINO = RAIZ / "dados" / "teste"
ESTATICOS = DESTINO / "ao_vivo_2024"
ELEICAO_ENSAIO = 619
CARGO_ENSAIO = 11
NOME_CARGO = "prefeito"


def escolher_regioes(mapa: dict, quantas: int) -> list[tuple[str, str, list]]:
    """Regiões com mais seções primeiro, e uma de uma seção só no fim.

    As duas pontas interessam: a de muitas urnas exercita o paralelismo e a soma;
    a de uma urna é o caso mais comum do país (52% das regiões têm até 4 seções).
    """
    candidatas = [(ibge, conteudo["municipio_tse"], regiao, secoes)
                  for ibge, conteudo in mapa.items()
                  for regiao, secoes in conteudo["regioes"].items()]
    candidatas.sort(key=lambda c: -len(c[3]))
    escolhidas = candidatas[:quantas - 1]
    de_uma = [c for c in candidatas if len(c[3]) == 1]
    if de_uma:
        escolhidas.append(de_uma[0])
    return escolhidas


def esperado(caminho_zip: Path, mapa: dict, escolhidas: list) -> list[dict]:
    """Lê todos os boletins uma vez e soma por região, com a regra do JavaScript."""
    por_secao: dict[tuple[str, int, int], bu.Boletim] = {}
    with zipfile.ZipFile(caminho_zip) as z:
        for nome in z.namelist():
            try:
                boletim = bu.ler(z.read(nome))
            except bu.BoletimInvalido:
                continue
            por_secao[(boletim.municipio, boletim.zona, boletim.secao)] = boletim

    casos = []
    for ibge, municipio_tse, regiao, secoes in escolhidas:
        locais = {local for _zona, local, _secao in secoes}
        por_votavel: dict[str, int] = {}
        comparecimento = urnas = 0
        for zona, _local, secao in secoes:
            boletim = por_secao.get((municipio_tse, zona, secao))
            if boletim is None:
                continue
            if boletim.local not in locais:          # o boletim manda
                continue
            if bu.conferir(boletim):
                continue
            cargos = [c for c in boletim.eleicoes.get(ELEICAO_ENSAIO, [])
                      if c.codigo == CARGO_ENSAIO]
            if not cargos:
                continue
            urnas += 1
            for cargo in cargos:
                comparecimento += cargo.comparecimento
                for voto in cargo.votos:
                    if voto.tipo in ("branco", "nulo"):
                        continue
                    chave = f"Número {voto.numero}"
                    por_votavel[chave] = por_votavel.get(chave, 0) + voto.quantidade
        if not urnas:
            continue
        casos.append({
            "id_regiao": int(regiao), "municipio_ibge": ibge, "uf": mapa[ibge]["uf"],
            "cargo": NOME_CARGO, "urnas": urnas, "comparecimento": comparecimento,
            "por_votavel": por_votavel,
        })
    return casos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", type=Path,
                        default=RAIZ / "dados" / "bruto" / "bu" / "RR.zip")
    parser.add_argument("--regioes", type=int, default=3)
    parser.add_argument("--mostrar", action="store_true")
    args = parser.parse_args()

    if not (ESTATICOS / "config.json").exists():
        sys.exit(f"gere os estáticos do ensaio primeiro:\n"
                 f"  python coleta_2026/ao_vivo.py --ano 2024 --eleicao 619 "
                 f"--ciclo ele2024 --pleito 452 --uf RR --cargo 11 "
                 f"--destino {ESTATICOS}")
    if not args.zip.exists():
        sys.exit(f"não achei {args.zip}")

    mapa = {caminho.stem: json.loads(caminho.read_text(encoding="utf-8"))
            for caminho in (ESTATICOS / "secoes").glob("*.json")}
    escolhidas = escolher_regioes(mapa, args.regioes)
    casos = esperado(args.zip, mapa, escolhidas)
    (DESTINO / "ao_vivo_esperado.json").write_text(
        json.dumps({"casos": casos}, ensure_ascii=False), encoding="utf-8")
    print(f"=== camada ao vivo — {len(casos)} região(ões) de ensaio")
    for caso in casos:
        print(f"  região {caso['id_regiao']} ({caso['municipio_ibge']}): "
              f"{caso['urnas']} urnas, {caso['comparecimento']:,} votos, "
              f"{len(caso['por_votavel'])} votáveis")

    from playwright.sync_api import sync_playwright

    porta = teste_bu_js.porta_livre()
    servidor = teste_bu_js.servir(porta)
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=not args.mostrar)
            pagina = navegador.new_page()
            erros: list[str] = []
            pagina.on("pageerror", lambda e: erros.append(str(e)))
            pagina.goto(f"http://127.0.0.1:{porta}/site/dev/teste_ao_vivo.html")
            pagina.wait_for_function("document.title !== 'camada ao vivo'", timeout=180_000)
            resultado = pagina.inner_text("#saida")
            titulo = pagina.title()
            navegador.close()
    finally:
        servidor.shutdown()

    print()
    print(resultado)
    for erro in erros:
        print(f"  erro de página: {erro}")
    if titulo != "ok" or erros:
        sys.exit(1)


if __name__ == "__main__":
    main()

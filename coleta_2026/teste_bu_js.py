"""Compara o parser do navegador com o parser do Python, em boletins reais.

    python coleta_2026/teste_bu_js.py
    python coleta_2026/teste_bu_js.py --zip dados/bruto/bu/RR.zip --limite 200

O parser que vai ao ar na noite da apuração roda no navegador de quem consulta
(`site/js/bu.js`). Ele é um porte de `coleta_2026/bu.py`, e porte é onde moram os
bugs silenciosos: JavaScript devolve `undefined` onde Python levanta exceção, e
`0x80` lido com o sinal errado dá um número plausível.

Então o teste é por equivalência: para cada boletim real, as duas
implementações têm de produzir a **mesma forma canônica** — identificação da
seção e, depois, cada eleição, cada cargo e cada votável na ordem do arquivo.
Igual, não parecido.

O que este script faz:

1. lê os boletins com o Python e grava `dados/teste/bus_rr.json` (bytes em base64
   mais a forma canônica e a saída de `conferir`);
2. grava também `bus_recusa.json`, com arquivos que o Python recusa, para
   conferir que o JavaScript recusa os mesmos;
3. sobe um servidor local na raiz do projeto — `file://` não permite `fetch`;
4. abre `site/dev/teste_bu.html` com o Chromium do playwright, lê o resultado da
   página e sai com código 1 se houver qualquer falha.

A pasta `dados/` é ignorada pelo git, então nada disso entra no repositório.
"""

from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import socket
import socketserver
import sys
import threading
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bu  # noqa: E402

DESTINO = RAIZ / "dados" / "teste"
PADRAO = RAIZ / "dados" / "bruto" / "bu" / "RR.zip"


def canonico(boletim: bu.Boletim) -> str:
    """A forma que as duas implementações têm de produzir igual."""
    partes = ["|".join(str(x) for x in (boletim.municipio, boletim.zona,
                                        boletim.local, boletim.secao, boletim.pleito))]
    for codigo in sorted(boletim.eleicoes):
        partes.append(f"e{codigo}")
        for cargo in sorted(boletim.eleicoes[codigo], key=lambda c: c.codigo):
            partes.append(f"c{cargo.codigo}|{cargo.comparecimento}")
            for voto in cargo.votos:
                partes.append("|".join(str(x) for x in (
                    voto.tipo,
                    "-" if voto.partido is None else voto.partido,
                    "-" if voto.numero is None else voto.numero,
                    voto.quantidade)))
    return "\n".join(partes)


FIXTURES = RAIZ / "tests" / "fixtures"


def um_caso(nome: str, dados: bytes) -> dict:
    boletim = bu.ler(dados)
    return {
        "nome": nome,
        "bytes": base64.b64encode(dados).decode("ascii"),
        "canonico": canonico(boletim),
        "problemas": bu.conferir(boletim),
    }


def montar_casos(caminho_zip: Path, limite: int | None) -> list[dict]:
    casos = []
    with zipfile.ZipFile(caminho_zip) as z:
        nomes = z.namelist()[:limite] if limite else z.namelist()
        for nome in nomes:
            casos.append(um_caso(nome, z.read(nome)))

    # Os boletins de exemplo do TSE, que são de outro formato: o de 2022, com
    # duas eleições e cinco cargos. Se o JavaScript lê os dois, a leitura não
    # depende do layout de um ano só — e é essa a aposta de domingo.
    for caminho in sorted(FIXTURES.glob("*.bu")) + sorted(FIXTURES.glob("*.dat")):
        casos.append(um_caso(f"fixture/{caminho.name}", caminho.read_bytes()))
    return casos


def montar_recusas(caminho_zip: Path) -> dict[str, str]:
    """Arquivos que o Python recusa — o JavaScript tem de recusar os mesmos.

    Saem de um boletim real, estragado de propósito: é o que mais se aproxima de
    um formato diferente do esperado.
    """
    with zipfile.ZipFile(caminho_zip) as z:
        bom = z.read(z.namelist()[0])

    estragados = {
        "vazio": b"",
        "não é DER": b"isto nao e um boletim",
        "cortado na metade": bom[: len(bom) // 2],
        "só os primeiros 20 bytes": bom[:20],
        "um byte a menos no fim": bom[:-1],
        "tamanho adulterado": bom[:2] + b"\xff" + bom[3:],
    }
    saida = {}
    for rotulo, dados in estragados.items():
        try:
            bu.ler(dados)
        except bu.BoletimInvalido:
            saida[rotulo] = base64.b64encode(dados).decode("ascii")
        else:
            print(f"  [aviso] o Python NÃO recusa '{rotulo}' — fora do teste")
    return saida


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def servir(porta: int) -> socketserver.TCPServer:
    manipulador = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(RAIZ))
    servidor = socketserver.TCPServer(("127.0.0.1", porta), manipulador)
    servidor.daemon_threads = True
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", type=Path, default=PADRAO)
    parser.add_argument("--limite", type=int, help="só os N primeiros boletins")
    parser.add_argument("--mostrar", action="store_true", help="abre o navegador visível")
    args = parser.parse_args()

    if not args.zip.exists():
        sys.exit(f"não achei {args.zip} — aponte com --zip um zip de boletins")

    print(f"=== bu.js × bu.py — {args.zip.name}")
    casos = montar_casos(args.zip, args.limite)
    recusas = montar_recusas(args.zip)
    DESTINO.mkdir(parents=True, exist_ok=True)
    (DESTINO / "bus_rr.json").write_text(json.dumps(casos), encoding="utf-8")
    (DESTINO / "bus_recusa.json").write_text(json.dumps(recusas), encoding="utf-8")
    tamanho = (DESTINO / "bus_rr.json").stat().st_size / 1e6
    print(f"  {len(casos):,} boletins lidos pelo Python | {tamanho:.1f} MB de entrada")
    print(f"  {len(recusas)} caso(s) de recusa")

    from playwright.sync_api import sync_playwright

    porta = porta_livre()
    servidor = servir(porta)
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=not args.mostrar)
            pagina = navegador.new_page()
            erros = []
            pagina.on("pageerror", lambda e: erros.append(str(e)))
            pagina.goto(f"http://127.0.0.1:{porta}/site/dev/teste_bu.html")
            pagina.wait_for_function("document.title !== 'bu.js contra o parser em Python'",
                                     timeout=180_000)
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

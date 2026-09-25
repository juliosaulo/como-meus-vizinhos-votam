"""Consulta de referência: endereço → região → resultados.

Lê apenas os arquivos de `publicado/`, exatamente como o site fará. Serve para
três coisas: conferir o dado à mão, servir de especificação executável para o
front-end (a lógica aqui é a mesma que o JavaScript precisa fazer) e ser o alvo
dos testes automatizados.

A sequência de decisão, em ordem:

0. **Normalizar.** Texto em maiúsculas sem acento. Número vazio, não numérico ou
   **zero** conta como "sem número" — no CNEFE, zero é a marca de S/N.
1. **Rua de região única** → responde, `exata`. Número e bairro são irrelevantes.
2. **Bairro informado** → restringe as regiões candidatas às daquele bairro. Se
   o bairro não existe naquela rua, é ignorado (`bairro_ignorado`). Se sobra uma
   região só, responde `exata`.
3. **Número informado** → busca binária no índice de trechos. Se a região
   encontrada está entre as candidatas, responde: `exata` quando o número é o
   início de um trecho, `interpolada` quando cai entre dois. Se o trecho
   encontrado é de outro pedaço da rua (outro bairro), caminha para os lados até
   o primeiro trecho compatível; não havendo nenhum, descarta o número e marca
   `conflito_numero_bairro`.
4. **Sem número** → a região de maior peso entre as candidatas, `provavel`.
5. **Sem rua** (`consultar_por_bairro`) → as regiões daquele bairro no município.

A resposta sempre traz `opcoes`: a lista de regiões possíveis com a fração de
endereços de cada uma, ordenada da mais provável para a menos. Mesmo quando a
resposta é única — assim o site não precisa de dois caminhos de código.

Uso:
    python consulta_regiao.py --municipio 1100015 --rua "AVENIDA BRASIL" --numero 2338
    python consulta_regiao.py --municipio 1100205 --rua "RUA SEM DENOMINACAO" --bairro ABUNA
    python consulta_regiao.py --municipio 1100205 --bairro ABUNA
    python consulta_regiao.py --municipio 1100015 --listar-ruas BRASIL
"""

from __future__ import annotations

import argparse
import bisect
import json
import unicodedata
from pathlib import Path

import config


def normalizar(texto: str) -> str:
    """Maiúsculas, sem acento — para comparar o que o usuário digita com o índice."""
    texto = str(texto).strip().upper()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def carregar_ruas(cd_municipio: str) -> dict[str, dict]:
    caminho = config.DIR_PUBLICADO / "ruas" / f"{cd_municipio}.json"
    if not caminho.exists():
        raise FileNotFoundError(f"município {cd_municipio} não publicado ({caminho})")
    return {r["nome"]: r for r in json.loads(caminho.read_text(encoding="utf-8"))["ruas"]}


def carregar_regioes(cd_municipio: str) -> dict:
    caminho = config.DIR_PUBLICADO / "regioes" / f"{cd_municipio}.json"
    return json.loads(caminho.read_text(encoding="utf-8"))


def carregar_bairros(cd_municipio: str) -> dict[str, list]:
    caminho = config.DIR_PUBLICADO / "bairros" / f"{cd_municipio}.json"
    if not caminho.exists():
        return {}
    return {b["nome"]: b["regioes"] for b in json.loads(caminho.read_text(encoding="utf-8"))["bairros"]}


def _pares(lista) -> list[tuple[int, float]]:
    return [(int(r), float(f)) for r, f in lista]


def opcoes_da_rua(rua: dict) -> list[tuple[int, float]]:
    """Regiões da rua, com peso. Rua de região única não publica a lista."""
    if rua.get("regioes"):
        return _pares(rua["regioes"])
    return [(int(rua["regiao_provavel"]), float(rua.get("confianca", 1.0)))]


def opcoes_do_bairro(rua: dict, bairro: str) -> list[tuple[int, float]] | None:
    """Regiões daquele bairro dentro da rua, ou None se o bairro não estiver lá."""
    alvo = normalizar(bairro)
    for nome, regioes in rua.get("bairros", []):
        if normalizar(nome) == alvo:
            return _pares(regioes)
    return None


def buscar_no_trecho(trechos: list, numero: int, candidatas: set[int]) -> tuple[int, bool] | None:
    """Região do trecho que contém o número, limitada às regiões candidatas.

    Devolve (id_regiao, exata) ou None. `exata` é verdadeiro quando o número é
    exatamente o início de um trecho. Quando o trecho encontrado não está entre
    as candidatas — o número existe noutro pedaço da rua, de outro bairro —, a
    busca caminha para os dois lados até o primeiro trecho compatível.
    """
    if not trechos:
        return None
    inicios = [t[0] for t in trechos]
    pos = max(bisect.bisect_right(inicios, numero) - 1, 0)
    if trechos[pos][1] in candidatas:
        return trechos[pos][1], inicios[pos] == numero
    for passo in range(1, len(trechos)):
        for i in (pos - passo, pos + passo):
            if 0 <= i < len(trechos) and trechos[i][1] in candidatas:
                return trechos[i][1], False
    return None


def resolver_regiao(rua: dict, numero: int | None = None, bairro: str | None = None) -> dict:
    """Região de um endereço, e o quanto essa resposta é confiável."""
    numero = numero if numero and numero > 0 else None  # zero é S/N, não um número
    resposta = {"bairro_ignorado": False, "conflito_numero_bairro": False}

    if rua["n_regioes"] == 1:
        opcoes = opcoes_da_rua(rua)
        return {**resposta, "id_regiao": opcoes[0][0], "confianca": "exata", "opcoes": opcoes}

    opcoes = None
    if bairro:
        opcoes = opcoes_do_bairro(rua, bairro)
        if opcoes is None:
            resposta["bairro_ignorado"] = True
    if opcoes is None:
        opcoes = opcoes_da_rua(rua)

    candidatas = {r for r, _ in opcoes}
    if len(candidatas) == 1:
        return {**resposta, "id_regiao": opcoes[0][0], "confianca": "exata", "opcoes": opcoes}

    if numero is not None:
        achado = buscar_no_trecho(rua.get("trechos") or [], numero, candidatas)
        if achado is not None:
            id_regiao, exata = achado
            return {
                **resposta, "id_regiao": id_regiao,
                "confianca": "exata" if exata else "interpolada", "opcoes": opcoes,
            }
        resposta["conflito_numero_bairro"] = bool(bairro and not resposta["bairro_ignorado"])

    return {**resposta, "id_regiao": opcoes[0][0], "confianca": "provavel", "opcoes": opcoes}


def _decorar(opcoes: list[tuple[int, float]], municipio: dict) -> list[dict]:
    """Acrescenta nome e endereço do local de votação a cada opção."""
    enfeitadas = []
    for id_regiao, fracao in opcoes:
        regiao = municipio["regioes"].get(str(id_regiao), {})
        enfeitadas.append({
            "id_regiao": id_regiao,
            "fracao": round(fracao, 3),
            "local": regiao.get("local"),
            "endereco": regiao.get("endereco"),
        })
    return enfeitadas


def consultar(cd_municipio: str, nome_rua: str, numero: int | None = None,
              bairro: str | None = None) -> dict:
    ruas = carregar_ruas(cd_municipio)

    rua = ruas.get(nome_rua)
    if rua is None:  # tenta de novo ignorando acento e caixa
        indice = {normalizar(k): k for k in ruas}
        chave = indice.get(normalizar(nome_rua))
        if chave is None:
            raise KeyError(f"rua {nome_rua!r} não encontrada em {cd_municipio}")
        rua = ruas[chave]

    achado = resolver_regiao(rua, numero, bairro)
    municipio = carregar_regioes(cd_municipio)
    return {
        "municipio": municipio["municipio"],
        "rua": rua["nome"],
        "numero": numero,
        "bairro": bairro,
        "n_regioes_na_rua": rua["n_regioes"],
        "bairros_da_rua": [nome for nome, _ in rua.get("bairros", [])],
        "regiao": municipio["regioes"].get(str(achado["id_regiao"])),
        "opcoes": _decorar(achado["opcoes"], municipio),
        **{k: achado[k] for k in ("id_regiao", "confianca", "bairro_ignorado", "conflito_numero_bairro")},
    }


def consultar_por_bairro(cd_municipio: str, bairro: str) -> dict:
    """Caminho de quem não sabe o nome da rua: município + bairro."""
    bairros = carregar_bairros(cd_municipio)
    chave = {normalizar(k): k for k in bairros}.get(normalizar(bairro))
    if chave is None:
        raise KeyError(f"bairro {bairro!r} não encontrado em {cd_municipio}")

    opcoes = _pares(bairros[chave])
    municipio = carregar_regioes(cd_municipio)
    return {
        "municipio": municipio["municipio"],
        "rua": None,
        "numero": None,
        "bairro": chave,
        "id_regiao": opcoes[0][0],
        "confianca": "exata" if len(opcoes) == 1 else "provavel",
        "bairro_ignorado": False,
        "conflito_numero_bairro": False,
        "n_regioes_na_rua": None,
        "bairros_da_rua": [],
        "regiao": municipio["regioes"].get(str(opcoes[0][0])),
        "opcoes": _decorar(opcoes, municipio),
    }


def imprimir(resultado: dict) -> None:
    mun, reg = resultado["municipio"], resultado["regiao"]
    onde = resultado["rua"] or f"bairro {resultado['bairro']}"
    if resultado["rua"] and resultado["bairro"]:
        onde += f" ({resultado['bairro']})"
    print(f"\n{mun['nome']}/{mun['uf']} — {onde}, {resultado['numero'] or 's/n'}")
    # A lista de opções só é mostrada quando a resposta é `provavel`: com número
    # exato ou interpolado, as outras regiões são de outros pedaços da rua, não
    # alternativas para este endereço. O site deve seguir a mesma regra.
    indeciso = resultado["confianca"] == "provavel"
    sufixo = f" ({len(resultado['opcoes'])} locais possíveis)" if indeciso else ""
    print(f"  confiança: {resultado['confianca']}{sufixo}")
    if resultado["bairro_ignorado"]:
        print("  [aviso] o bairro informado não existe nesta rua — ignorado")
    if resultado["conflito_numero_bairro"]:
        print("  [aviso] o número informado não existe no trecho deste bairro — ignorado")

    if reg is None:
        print("  sem dados publicados para esta região")
        return

    print(f"\n  Você vota em: {reg['local']}")
    print(f"  {reg['endereco']}")
    print(f"  mapa: {reg['lat']}, {reg['lon']}")
    if reg["outros_locais"]:
        print(f"  (outros locais no mesmo ponto: {', '.join(reg['outros_locais'])})")

    if indeciso and len(resultado["opcoes"]) > 1:
        print("\n  Outros locais possíveis para este endereço:")
        for o in resultado["opcoes"][1:6]:
            print(f"    {o['fracao'] * 100:5.1f}%  {o['local']} — {o['endereco']}")

    presidente = reg["resultados"].get("presidente", {})
    for ano in sorted(presidente):
        for turno in sorted(presidente[ano]):
            rotulo = "1º turno" if turno == "1" else "2º turno"
            print(f"\n  Presidente {ano} · {rotulo}")
            for c in presidente[ano][turno][:4]:
                print(f"    {c['pct']:>6.2f}%  {c['nome']} ({c['partido']})")

    deputados = reg["resultados"].get("deputado_federal", {})
    for ano in sorted(deputados):
        for turno in sorted(deputados[ano]):
            print(f"\n  Deputado Federal {ano} — mais votados aqui")
            for c in deputados[ano][turno]:
                print(f"    {c['pct']:>6.2f}%  {c['nome']} ({c['partido']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Consulta a região de um endereço.")
    parser.add_argument("--municipio", required=True, help="código IBGE de 7 dígitos")
    parser.add_argument("--rua")
    parser.add_argument("--numero", type=int)
    parser.add_argument("--bairro")
    parser.add_argument("--listar-ruas", help="lista ruas que contenham este texto")
    parser.add_argument("--listar-bairros", action="store_true", help="lista os bairros do município")
    args = parser.parse_args()

    if args.listar_ruas:
        alvo = normalizar(args.listar_ruas)
        achadas = [n for n in carregar_ruas(args.municipio) if alvo in normalizar(n)]
        print(f"{len(achadas)} rua(s):")
        for nome in sorted(achadas)[:40]:
            print(f"  {nome}")
        return

    if args.listar_bairros:
        bairros = carregar_bairros(args.municipio)
        print(f"{len(bairros)} bairro(s):")
        for nome in sorted(bairros)[:60]:
            print(f"  {nome} ({len(bairros[nome])} região(ões))")
        return

    if args.rua:
        imprimir(consultar(args.municipio, args.rua, args.numero, args.bairro))
    elif args.bairro:
        imprimir(consultar_por_bairro(args.municipio, args.bairro))
    else:
        parser.error("informe --rua, --bairro, --listar-ruas ou --listar-bairros")


if __name__ == "__main__":
    main()

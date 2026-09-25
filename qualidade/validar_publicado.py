"""Confere os arquivos de `publicado/` — o produto, e não as etapas que o geram.

As guardas dos passos checam cada transformação por dentro. Esta validação olha
o que sai: um arquivo pode estar sintaticamente correto para o Python e ainda
assim ser inútil para o site, por não abrir num parser estrito, por citar uma
região que não existe naquele município ou por trazer percentuais que não
fecham.

São quatro verificações:

1. **JSON estrito.** Todo arquivo abre num parser que recusa `NaN` e `Infinity`
   — o Python aceita os dois na leitura e na escrita, o navegador não.
2. **Integridade referencial.** Toda região citada em `ruas/` e `bairros/`
   existe no `regioes/` do mesmo município; todo município listado tem os seus
   arquivos; `ufs.json` bate com as listas de municípios.
3. **Percentuais.** Para Presidente, onde todos os candidatos são publicados, a
   soma dos percentuais fecha 100; nenhum percentual sai de 0–100; nenhum voto
   é negativo.
4. **Listas de regiões.** As frações de cada lista somam 1 e vêm em ordem
   decrescente — é a ordem em que o site mostra as opções.

Uso:
    python qualidade/validar_publicado.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade.validacoes import ValidacaoFalhou

TOLERANCIA_PCT = 0.5       # arredondamento de 2 casas em até ~13 candidatos
TOLERANCIA_FRACAO = 0.02   # idem, em frações de 3 casas


def tolerancia_da_lista(n: int) -> float:
    """Quanto a soma de uma lista de frações pode se afastar de 1.

    As frações são publicadas com 3 casas, então cada item perde até 0,0005 —
    e uma região com peso desprezível vira 0,0. Numa rua com centenas de
    regiões (as vias sem nome de São Paulo chegam a 715) isso soma. A
    tolerância acompanha o tamanho da lista em vez de ser fixa.
    """
    return max(TOLERANCIA_FRACAO, 0.0005 * n)


def _recusa_constante(nome: str):
    raise ValueError(f"valor {nome} não é JSON válido")


def ler_json_estrito(caminho: Path):
    """Lê um JSON recusando `NaN` e `Infinity`, como faz o navegador."""
    return json.loads(caminho.read_text(encoding="utf-8"), parse_constant=_recusa_constante)


def problemas_da_rua(rua: dict, regioes_validas: set[int]) -> list[str]:
    nome = rua.get("nome", "?")
    problemas = []

    citadas = {int(rua["regiao_provavel"])}
    citadas |= {int(t[1]) for t in rua.get("trechos", [])}
    citadas |= {int(r) for r, _ in rua.get("regioes", [])}
    for _, lista in rua.get("bairros", []):
        citadas |= {int(r) for r, _ in lista}
    faltando = citadas - regioes_validas
    if faltando:
        problemas.append(f"rua {nome}: regiões inexistentes no município {sorted(faltando)}")

    if rua.get("n_regioes", 1) > 1 and not rua.get("regioes"):
        problemas.append(f"rua {nome}: ambígua, mas sem a lista de regiões")

    for rotulo, lista in [("regioes", rua.get("regioes"))] + \
                         [(f"bairro {b}", l) for b, l in rua.get("bairros", [])]:
        if not lista:
            continue
        fracoes = [float(f) for _, f in lista]
        if abs(sum(fracoes) - 1) > tolerancia_da_lista(len(fracoes)):
            problemas.append(f"rua {nome}, {rotulo}: frações somam {sum(fracoes):.3f}, não 1")
        if fracoes != sorted(fracoes, reverse=True):
            problemas.append(f"rua {nome}, {rotulo}: lista fora de ordem decrescente")
    return problemas


def problemas_da_regiao(id_regiao: str, regiao: dict) -> list[str]:
    problemas = []
    resultados = regiao.get("resultados", {})

    for cargo, por_ano in resultados.items():
        if cargo in ("nao_nominal", "total_votos"):
            continue
        for ano, por_turno in por_ano.items():
            for turno, candidatos in por_turno.items():
                onde = f"região {id_regiao}, {cargo} {ano}/{turno}"
                for c in candidatos:
                    if not 0 <= c["pct"] <= 100:
                        problemas.append(f"{onde}: pct fora de 0–100 ({c['nome']}: {c['pct']})")
                    if c["votos"] < 0:
                        problemas.append(f"{onde}: votos negativos ({c['nome']})")
                # Presidente publica todos os candidatos, então a soma fecha 100.
                if cargo == "presidente":
                    soma = sum(c["pct"] for c in candidatos)
                    if abs(soma - 100) > TOLERANCIA_PCT:
                        problemas.append(f"{onde}: percentuais somam {soma:.2f}, não 100")

    for cargo, por_ano in resultados.get("nao_nominal", {}).items():
        for ano, por_turno in por_ano.items():
            for turno, tipos in por_turno.items():
                for tipo, valor in tipos.items():
                    if not 0 <= valor["pct"] <= 100:
                        problemas.append(
                            f"região {id_regiao}, {cargo} {ano}/{turno}: {tipo} com pct "
                            f"{valor['pct']} fora de 0–100"
                        )
    return problemas


def main() -> None:
    print("=== validação dos arquivos publicados ===")
    pub = config.DIR_PUBLICADO
    problemas: list[str] = []
    arquivos = 0

    ufs = ler_json_estrito(pub / "ufs.json")
    arquivos += 1
    for uf in ufs:
        caminho_municipios = pub / "municipios" / f"{uf['sigla']}.json"
        if not caminho_municipios.exists():
            problemas.append(f"{uf['sigla']}: sem arquivo de municípios")
            continue
        municipios = ler_json_estrito(caminho_municipios)
        arquivos += 1
        if len(municipios) != uf["n_municipios"]:
            problemas.append(
                f"{uf['sigla']}: ufs.json diz {uf['n_municipios']} municípios, "
                f"o arquivo tem {len(municipios)}"
            )

        for municipio in municipios:
            cd = municipio["cd"]
            caminho_regioes, caminho_ruas = pub / "regioes" / f"{cd}.json", pub / "ruas" / f"{cd}.json"
            if not caminho_regioes.exists() or not caminho_ruas.exists():
                problemas.append(f"{cd} ({municipio['nome']}/{uf['sigla']}): faltam arquivos")
                continue

            regioes = ler_json_estrito(caminho_regioes)["regioes"]
            ruas = ler_json_estrito(caminho_ruas)["ruas"]
            arquivos += 2
            validas = {int(k) for k in regioes}

            for id_regiao, regiao in regioes.items():
                problemas += problemas_da_regiao(id_regiao, regiao)
            for rua in ruas:
                problemas += problemas_da_rua(rua, validas)

            caminho_bairros = pub / "bairros" / f"{cd}.json"
            if caminho_bairros.exists():
                arquivos += 1
                for bairro in ler_json_estrito(caminho_bairros)["bairros"]:
                    citadas = {int(r) for r, _ in bairro["regioes"]} - validas
                    if citadas:
                        problemas.append(
                            f"{cd}, bairro {bairro['nome']}: regiões inexistentes {sorted(citadas)}"
                        )
                    fracoes = [float(f) for _, f in bairro["regioes"]]
                    if abs(sum(fracoes) - 1) > tolerancia_da_lista(len(fracoes)):
                        problemas.append(
                            f"{cd}, bairro {bairro['nome']}: frações somam {sum(fracoes):.3f}"
                        )
        print(f"  {uf['sigla']}: {len(municipios):,} municípios conferidos")

    print(f"\n  arquivos lidos: {arquivos:,}")
    if problemas:
        amostra = "\n".join(f"  - {p}" for p in problemas[:20])
        raise ValidacaoFalhou(
            f"{len(problemas):,} problema(s) nos arquivos publicados:\n{amostra}"
            + ("\n  ..." if len(problemas) > 20 else "")
        )
    print("  nenhum problema encontrado")


if __name__ == "__main__":
    main()

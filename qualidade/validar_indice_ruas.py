"""Validação ponta a ponta: o índice publicado devolve a região certa?

Todos os outros testes checam etapas isoladas. Este reproduz o caminho que o
usuário do site vai percorrer — município, rua e número → região — e compara com
a verdade espacial (o local de votação mais próximo da coordenada real daquele
endereço, calculada direto do CNEFE).

É o número que importa para quem consome o site, porque mede o efeito acumulado
de todas as aproximações do pipeline juntas: a compressão do índice, o empate
entre lados da rua, o endereço sem número.

Uso:
    python qualidade/validar_indice_ruas.py [--por-uf 5000]
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import consulta_regiao

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
_modulo_31 = __import__("31_atribuir_endereco_regiao")
montar_logradouro = _modulo_31.montar_logradouro
arquivo_cnefe = _modulo_31.arquivo_cnefe
COLUNAS_CNEFE = _modulo_31.COLUNAS_CNEFE


def consultar_indice(ruas_do_municipio: dict, logradouro: str,
                     numero: int | None = None, bairro: str | None = None) -> int | None:
    """Consulta pela mesma função que o site usa — sem reimplementar a busca.

    Validar contra uma segunda implementação da consulta testaria a cópia, não o
    índice.
    """
    rua = ruas_do_municipio.get(logradouro)
    if rua is None:
        return None
    return consulta_regiao.resolver_regiao(rua, numero, bairro)["id_regiao"]


def amostra_de_enderecos(uf: str, n: int) -> pd.DataFrame:
    """Sorteia n endereços com coordenada, lendo o CNEFE em blocos.

    O arquivo inteiro não cabe na memória em UF grande (o de SP tem 3,8 GB). A
    primeira passada só conta as linhas; a segunda guarda a mesma fração de
    cada bloco, com folga de 3×, e o sorteio final sai dessa pré-amostra.
    Reprodutível pela semente.
    """
    with zipfile.ZipFile(arquivo_cnefe(uf)) as z, z.open(z.namelist()[0]) as f:
        linhas = sum(buf.count(b"\n") for buf in iter(lambda: f.read(1 << 24), b""))
    fracao = min(1.0, 3 * n / max(linhas, 1))

    partes = []
    with zipfile.ZipFile(arquivo_cnefe(uf)) as z, z.open(z.namelist()[0]) as f:
        for i, bloco in enumerate(pd.read_csv(
            f, sep=";", encoding="latin1", dtype=str, usecols=COLUNAS_CNEFE, chunksize=400_000
        )):
            partes.append(bloco.sample(frac=fracao, random_state=42 + i))
    df = pd.concat(partes, ignore_index=True)
    df = df.drop_duplicates(subset=["COD_UNICO_ENDERECO", "COD_ESPECIE"])
    df["latitude"] = pd.to_numeric(df["LATITUDE"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])
    df = df.sample(min(n, len(df)), random_state=42).copy()
    df["cd_municipio_ibge"] = df["COD_MUNICIPIO"].str.strip()
    df["logradouro"] = montar_logradouro(df)
    df["numero"] = pd.to_numeric(df["NUM_ENDERECO"], errors="coerce").fillna(0).astype("int64")
    df["bairro"] = df["DSC_LOCALIDADE"].fillna("").str.strip().str.upper()
    # O índice pesa por domicílio (ver passo 32), mas a amostra tem de tudo:
    # comércio, obra, escola. A medição sai separada para os dois, senão o
    # número que o site publica descreve uma população que não é a dele.
    df["domicilio"] = df["COD_ESPECIE"].isin(_modulo_31.ESPECIES_DOMICILIO)
    return df


def verdade_espacial(amostra: pd.DataFrame, regioes: pd.DataFrame) -> pd.Series:
    transformador = Transformer.from_crs(config.CRS_GEOGRAFICO, config.CRS_METRICO, always_xy=True)
    amostra = amostra.copy()
    amostra["x"], amostra["y"] = transformador.transform(
        amostra["longitude"].values, amostra["latitude"].values
    )
    resposta = pd.Series(index=amostra.index, dtype="object")
    for cd_municipio, grupo in amostra.groupby("cd_municipio_ibge"):
        alvo = regioes[regioes["cd_municipio_ibge"] == cd_municipio]
        if alvo.empty:
            continue
        arvore = cKDTree(alvo[["x", "y"]].values)
        _, indices = arvore.query(grupo[["x", "y"]].values, k=1)
        resposta.loc[grupo.index] = [int(alvo.iloc[i]["id_regiao"]) for i in indices]
    return resposta


def carregar_regioes_projetadas() -> pd.DataFrame:
    dim = pd.read_parquet(config.DIR_PUBLICADO / "regioes.parquet")
    transformador = Transformer.from_crs(config.CRS_GEOGRAFICO, config.CRS_METRICO, always_xy=True)
    dim["x"], dim["y"] = transformador.transform(
        dim["longitude_final"].values, dim["latitude_final"].values
    )
    return dim


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--por-uf", type=int, default=5000, help="endereços sorteados por UF")
    args = parser.parse_args()

    print("=== validação ponta a ponta do índice de ruas ===")
    regioes = carregar_regioes_projetadas()

    linhas = []
    for uf in config.ufs_para_processar():
        caminho_ruas = config.DIR_PUBLICADO / "ruas"
        amostra = amostra_de_enderecos(uf, args.por_uf)
        amostra["verdade"] = verdade_espacial(amostra, regioes)
        amostra = amostra.dropna(subset=["verdade"])

        # Os quatro caminhos que o site pode percorrer, sobre a MESMA amostra:
        # é o que diz quanto cada pergunta a mais ao usuário realmente compra.
        cache: dict[str, dict] = {}
        previsto: dict[str, list] = {"so_rua": [], "rua_bairro": [], "rua_numero": [], "completo": []}
        for linha in amostra.itertuples():
            if linha.cd_municipio_ibge not in cache:
                arquivo = caminho_ruas / f"{linha.cd_municipio_ibge}.json"
                cache[linha.cd_municipio_ibge] = (
                    {r["nome"]: r for r in json.load(open(arquivo, encoding="utf-8"))["ruas"]}
                    if arquivo.exists() else {}
                )
            ruas = cache[linha.cd_municipio_ibge]
            previsto["so_rua"].append(consultar_indice(ruas, linha.logradouro))
            previsto["rua_bairro"].append(consultar_indice(ruas, linha.logradouro, bairro=linha.bairro))
            previsto["rua_numero"].append(consultar_indice(ruas, linha.logradouro, linha.numero))
            previsto["completo"].append(
                consultar_indice(ruas, linha.logradouro, linha.numero, linha.bairro)
            )
        for nome, valores in previsto.items():
            amostra[nome] = valores

        achou = amostra["rua_numero"].notna()
        registro = {
            "uf": uf,
            "amostra": len(amostra),
            "rua_encontrada_pct": round(achou.mean() * 100, 2),
            # `acerto_pct` continua sendo o caminho rua + número, para poder
            # comparar com as medições anteriores.
            "acerto_pct": round(((amostra["rua_numero"] == amostra["verdade"]) & achou).mean() * 100, 2),
        }
        so_domicilios = amostra["domicilio"]
        registro["pct_domicilios_na_amostra"] = round(so_domicilios.mean() * 100, 2)
        for nome in previsto:
            certo = (amostra[nome] == amostra["verdade"]) & amostra[nome].notna()
            registro[f"acerto_{nome}_pct"] = round(certo.mean() * 100, 2)
            registro[f"acerto_{nome}_domicilios_pct"] = round(certo[so_domicilios].mean() * 100, 2)
        linhas.append(registro)
        print(f"  {uf}: {registro}")

    resumo = pd.DataFrame(linhas)
    destino = config.DIR_PUBLICADO / "validacao_indice_ruas.json"
    resumo.to_json(destino, orient="records", force_ascii=False, indent=2)
    print()
    print(f"  {'caminho':<24} {'todos':>8} {'domicílios':>12}")
    for nome, rotulo in (("so_rua", "só a rua"), ("rua_bairro", "rua + bairro"),
                         ("rua_numero", "rua + número"), ("completo", "rua + bairro + número")):
        print(f"  {rotulo:<24} {resumo[f'acerto_{nome}_pct'].mean():>7.2f}% "
              f"{resumo[f'acerto_{nome}_domicilios_pct'].mean():>11.2f}%")
    print(f"  salvo: {destino}")


if __name__ == "__main__":
    main()

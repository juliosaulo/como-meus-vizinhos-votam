"""Passo 10 — lê a base oficial de locais de votação do TSE.

O artefato de geocodificação (ver `dados_importados/PROVENIENCIA.md`) resolve a
coordenada de 83,9% dos locais de 2018 e 2022 e, por construção, não conhece os
locais de 2026. O TSE publica, para cada eleição, um arquivo com todas as seções
e o prédio onde funcionam — com nome, endereço, coordenada e eleitorado.

Esse arquivo entra aqui como **complemento**, não como substituto. A coordenada
casada com o CNEFE continua mandando onde existe, por dois motivos medidos:

- as duas fontes discordam acima de 5 km em 11,4% dos locais, e nesses casos o
  ponto que foge do município é o do TSE em 29% das vezes, contra 14% do nosso;
- a coordenada casada é, ela própria, um endereço do CNEFE, e é isso que faz de
  "o local mais próximo deste endereço" uma conta entre coisas do mesmo cadastro
  (ver `dist_min` no diagnóstico de distâncias).

O complemento vale, então, para o que o artefato não tem: os locais sem
coordenada e os que só existem em eleições mais novas.

Entrada:  dados/bruto/locais_oficiais/eleitorado_local_votacao_AAAA.zip
Saídas:   dados/intermediario/locais_oficiais.parquet   (um registro por local)
          dados/intermediario/eleitorado_local.parquet  (local × ano × turno)
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes

TAMANHO_BLOCO = 300_000

COLUNAS = [
    "AA_ELEICAO", "NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA",
    "NR_SECAO", "NR_LOCAL_VOTACAO", "NM_LOCAL_VOTACAO", "DS_ENDERECO",
    "NR_LATITUDE", "NR_LONGITUDE", "QT_ELEITOR_ELEICAO_FEDERAL",
]

# O voto no exterior não tem endereço correspondente no CNEFE e está fora do
# escopo do projeto, como no passo 11.
UF_EXTERIOR = "ZZ"


def arquivos_oficiais() -> list[Path]:
    pasta = config.DIR_BRUTO_LOCAIS_OFICIAIS
    arquivos = sorted(pasta.glob("eleitorado_local_votacao_*.zip"))
    if not arquivos:
        raise FileNotFoundError(
            f"nenhum arquivo de locais oficiais em {pasta}\n"
            "Baixe de https://cdn.tse.jus.br/estatistica/sead/odsele/eleitorado_locais_votacao/ "
            "(ver README, seção 'Dados de origem')."
        )
    return arquivos


def csvs_do_zip(z: zipfile.ZipFile) -> list[str]:
    """Os CSV que valem a pena ler.

    O TSE publica o mesmo conteúdo em dois formatos conforme o ano: um CSV
    nacional (2018, 2022) ou um por UF mais um 'BRASIL' que repete todos (2026).
    Ler os dois seria contar cada seção duas vezes.
    """
    csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
    por_uf = [n for n in csvs if "BRASIL" not in n.upper()]
    return por_uf if len(csvs) > 1 else csvs


def para_float(serie: pd.Series) -> pd.Series:
    """Coordenada do TSE: vírgula decimal em uns anos, ponto em outros."""
    return pd.to_numeric(serie.str.replace(",", ".", regex=False), errors="coerce")


def ler_arquivo(caminho: Path) -> pd.DataFrame:
    blocos = []
    with zipfile.ZipFile(caminho) as z:
        for nome in csvs_do_zip(z):
            with z.open(nome) as f:
                for bloco in pd.read_csv(
                    f, sep=";", dtype=str, usecols=COLUNAS, chunksize=TAMANHO_BLOCO,
                    encoding="latin-1",
                ):
                    bloco = bloco[bloco["SG_UF"] != UF_EXTERIOR]
                    if config.UFS_ALVO:
                        bloco = bloco[bloco["SG_UF"].isin(config.UFS_ALVO)]
                    if not bloco.empty:
                        blocos.append(bloco)
    if not blocos:
        return pd.DataFrame(columns=COLUNAS)
    return pd.concat(blocos, ignore_index=True)


def preparar(bruto: pd.DataFrame) -> pd.DataFrame:
    df = bruto.copy()
    df["cd_municipio_tse"] = df["CD_MUNICIPIO"].str.strip().str.zfill(5)
    df["nr_zona"] = df["NR_ZONA"].astype(int)
    df["nr_local"] = df["NR_LOCAL_VOTACAO"].astype(int)
    df["id_local_votacao"] = (
        df["SG_UF"].str.strip() + "_" + df["cd_municipio_tse"] + "_"
        + df["nr_zona"].astype(str) + "_" + df["nr_local"].astype(str)
    )
    df["ano_eleicao"] = df["AA_ELEICAO"].astype(int)
    df["turno"] = df["NR_TURNO"].astype(int)
    df["latitude"] = para_float(df["NR_LATITUDE"])
    df["longitude"] = para_float(df["NR_LONGITUDE"])
    # O TSE não deixa a coordenada vazia quando não a tem: publica zero ou o
    # sentinela -1 (quase todo o arquivo de 2018), e sobra ainda um punhado de
    # valores impossíveis. Nada disso é coordenada, e tratar como se fosse
    # colocaria locais no meio do oceano. Fora da caixa do Brasil vira ausente.
    invalida = (
        df["latitude"].isna() | df["longitude"].isna()
        | df["latitude"].abs().lt(0.001) | df["longitude"].abs().lt(0.001)
        | ~df["latitude"].between(*validacoes.CAIXA_BRASIL_LAT)
        | ~df["longitude"].between(*validacoes.CAIXA_BRASIL_LON)
    )
    df.loc[invalida, ["latitude", "longitude"]] = pd.NA
    df["qt_eleitores"] = pd.to_numeric(df["QT_ELEITOR_ELEICAO_FEDERAL"], errors="coerce").fillna(0).astype(int)
    return df


def montar_eleitorado(df: pd.DataFrame) -> pd.DataFrame:
    """Eleitorado por local, ano e turno — a soma das seções que funcionam ali."""
    eleitorado = (
        df.groupby(["id_local_votacao", "ano_eleicao", "turno"], as_index=False)["qt_eleitores"]
        .sum()
    )
    # Conservação: nenhuma seção pode sumir nem ser contada duas vezes.
    validacoes.checar_soma_preservada(
        df["qt_eleitores"].sum(), eleitorado["qt_eleitores"].sum(), "eleitorado por local"
    )
    validacoes.checar_chave_unica(
        eleitorado, ["id_local_votacao", "ano_eleicao", "turno"], "eleitorado por local"
    )
    return eleitorado


def montar_locais(df: pd.DataFrame) -> pd.DataFrame:
    """Um registro por local, com a descrição da eleição mais recente em que ele
    aparece — é a que reflete o prédio como ele está hoje."""
    com_coordenada = df[df["latitude"].notna()].copy()
    com_coordenada = com_coordenada.sort_values(
        ["id_local_votacao", "ano_eleicao", "turno"], ascending=[True, False, False]
    )
    locais = com_coordenada.groupby("id_local_votacao", as_index=False).first()[[
        "id_local_votacao", "SG_UF", "cd_municipio_tse", "NM_MUNICIPIO", "nr_zona", "nr_local",
        "NM_LOCAL_VOTACAO", "DS_ENDERECO", "latitude", "longitude", "ano_eleicao",
    ]].rename(columns={
        "SG_UF": "sg_uf", "NM_MUNICIPIO": "nm_municipio",
        "NM_LOCAL_VOTACAO": "nm_local_votacao", "DS_ENDERECO": "ds_endereco",
        "ano_eleicao": "ano_referencia",
    })

    validacoes.checar_chave_unica(locais, ["id_local_votacao"], "locais oficiais")
    validacoes.checar_coordenadas_no_brasil(
        locais["latitude"], locais["longitude"], "locais oficiais do TSE"
    )
    return locais


def relatar(df: pd.DataFrame, locais: pd.DataFrame, eleitorado: pd.DataFrame) -> None:
    sem_coord = df.loc[df["latitude"].isna(), "id_local_votacao"].nunique()
    print(f"  seções lidas: {len(df):,}")
    print(f"  locais distintos: {df['id_local_votacao'].nunique():,} "
          f"({sem_coord:,} sem coordenada no TSE)")
    print(f"  locais com coordenada: {len(locais):,}")
    for (ano, turno), grupo in eleitorado.groupby(["ano_eleicao", "turno"]):
        print(f"  eleitorado {ano}, {turno}º turno: {grupo['qt_eleitores'].sum():,} "
              f"em {len(grupo):,} locais")


def main() -> None:
    print("=== 10 · locais oficiais do TSE ===")
    config.garantir_pastas()

    partes = []
    for caminho in arquivos_oficiais():
        print(f"  lendo {caminho.name}…")
        partes.append(preparar(ler_arquivo(caminho)))
    df = pd.concat(partes, ignore_index=True)

    eleitorado = montar_eleitorado(df)
    locais = montar_locais(df)
    relatar(df, locais, eleitorado)

    saida_locais = config.DIR_INTERMEDIARIO / "locais_oficiais.parquet"
    saida_eleitorado = config.DIR_INTERMEDIARIO / "eleitorado_local.parquet"
    locais.to_parquet(saida_locais, index=False)
    eleitorado.to_parquet(saida_eleitorado, index=False)
    print()
    print(f"  salvo: {saida_locais}")
    print(f"  salvo: {saida_eleitorado}")


if __name__ == "__main__":
    main()

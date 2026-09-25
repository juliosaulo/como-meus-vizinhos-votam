"""Compara os locais de votação de um ano com o artefato geocodificado.

Rodar antes de trocar `config.ANO_REFERENCIA_MALHA` para um ano novo. A
coordenada de cada local vem do artefato importado (locais de 2018 e 2022),
ligada pelo `id_local_votacao`; este script mede o quanto isso ainda vale para
os locais do ano novo, tirados do próprio arquivo de votação por seção:

    mesmo_local                      id conhecido, com coordenada, e nome ou
                                     endereço iguais — a coordenada serve
    id_conhecido_nome_endereco_mudou id conhecido, mas nome E endereço
                                     diferentes — pode ser outro prédio, conferir
    id_conhecido_sem_coordenada      já não tinha coordenada em 2018/2022
    id_novo                          local que não existia — fica sem coordenada

Uso:
    python qualidade/comparar_locais.py --ano 2026

Saída: dados/intermediario/comparacao_locais_{ano}.csv (só os casos que não são
`mesmo_local`, com os dois nomes e endereços lado a lado).
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from consulta_regiao import normalizar
from qualidade import validacoes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
montar_id_local = __import__("21_processar_votacao").montar_id_local

COLUNAS = [
    "SG_UF", "CD_MUNICIPIO", "NR_ZONA", "NR_LOCAL_VOTACAO",
    "NM_LOCAL_VOTACAO", "DS_LOCAL_VOTACAO_ENDERECO", "CD_TIPO_ELEICAO",
]


def texto_comparavel(serie: pd.Series) -> pd.Series:
    """Sem acento, sem pontuação, espaços únicos."""
    return (
        serie.fillna("").map(normalizar)
        .str.replace(r"[^A-Z0-9 ]", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True).str.strip()
    )


def locais_do_ano(ano: int) -> pd.DataFrame:
    """Locais distintos do arquivo nacional de Presidente — toda seção vota para Presidente."""
    caminho = config.DIR_BRUTO_VOTACAO_PRESIDENTE / f"votacao_secao_{ano}_BR.zip"
    if not caminho.exists():
        raise FileNotFoundError(f"votação de {ano} não encontrada: {caminho}")

    ufs = set(config.ufs_para_processar())
    partes = []
    with zipfile.ZipFile(caminho) as z, z.open(f"votacao_secao_{ano}_BR.csv") as f:
        for bloco in pd.read_csv(
            f, sep=";", encoding="latin1", dtype=str, usecols=COLUNAS, chunksize=500_000
        ):
            bloco = bloco[bloco["SG_UF"].isin(ufs)]
            bloco = validacoes.filtrar_eleicao_ordinaria(bloco)
            if bloco.empty:
                continue
            bloco["CD_MUNICIPIO"] = validacoes.normalizar_codigo_municipio(bloco["CD_MUNICIPIO"])
            bloco["id_local_votacao"] = montar_id_local(bloco)
            partes.append(bloco.drop_duplicates("id_local_votacao"))

    if not partes:
        raise RuntimeError(f"nenhum local de {ano} nas UFs {sorted(ufs)}")
    return pd.concat(partes, ignore_index=True).drop_duplicates("id_local_votacao")


def classificar(locais: pd.DataFrame, artefato: pd.DataFrame) -> pd.DataFrame:
    df = locais.merge(
        artefato[["id_local_votacao", "nm_local_votacao_consolidado",
                  "ds_local_votacao_endereco_consolidado", "latitude_final", "longitude_final"]],
        on="id_local_votacao", how="left", indicator=True,
    )

    def iguais(a: str, b: str) -> pd.Series:
        ta, tb = texto_comparavel(df[a]), texto_comparavel(df[b])
        return (ta == tb) & (ta != "")  # dois campos vazios não provam nada

    mesmo_nome = iguais("NM_LOCAL_VOTACAO", "nm_local_votacao_consolidado")
    mesmo_endereco = iguais("DS_LOCAL_VOTACAO_ENDERECO", "ds_local_votacao_endereco_consolidado")

    df["situacao"] = np.select(
        [df["_merge"] == "left_only", df["latitude_final"].isna(), mesmo_nome | mesmo_endereco],
        ["id_novo", "id_conhecido_sem_coordenada", "mesmo_local"],
        default="id_conhecido_nome_endereco_mudou",
    )
    return df.drop(columns="_merge")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara os locais de um ano com o artefato geocodificado.")
    parser.add_argument("--ano", type=int, required=True)
    args = parser.parse_args()

    print(f"=== locais de {args.ano} × artefato geocodificado ===")
    artefato = pd.read_parquet(config.ARQ_LOCAIS_GEOCODIFICADOS)
    df = classificar(locais_do_ano(args.ano), artefato)

    tabela = pd.crosstab(df["SG_UF"], df["situacao"], margins=True, margins_name="total")
    pct = (tabela.div(tabela["total"], axis=0) * 100).round(1).drop(columns="total")
    print()
    print(tabela.to_string())
    print()
    print("  % dos locais:")
    print(pct.to_string())

    destino = config.DIR_INTERMEDIARIO / f"comparacao_locais_{args.ano}.csv"
    df[df["situacao"] != "mesmo_local"].sort_values(["situacao", "id_local_votacao"]).to_csv(
        destino, index=False, sep=";", encoding="utf-8-sig"
    )
    print()
    print(f"  casos para olhar: {destino}")


if __name__ == "__main__":
    main()

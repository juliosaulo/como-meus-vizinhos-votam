"""Passo 22 — leva os votos do local de votação para a região, na malha de referência.

Duas operações, nesta ordem:

1. **Consolidação por coordenada.** Locais de votação que dividem o mesmo prédio
   viram uma região só, e os votos são SOMADOS (urnas distintas, eleitores
   distintos). O oposto do que se faz com atributo de lugar, que é deduplicado.

2. **Realocação do histórico.** A malha de regiões é a do ano de referência —
   "onde a pessoa vota hoje". Locais que existiram em 2018 mas não no ano de
   referência têm seus votos atribuídos à região de referência mais próxima
   dentro do mesmo município. Sem isso, uma região apareceria no site sem
   histórico, e um voto de 2018 sumiria da tela.

   É uma aproximação, e das que importam: quando um local fecha e outro abre a
   dois quarteirões, tratá-los como a mesma área é razoável; quando a mudança é
   maior, o número de 2018 daquela região passa a descrever um território
   levemente diferente. Fica registrado em `publicado/cobertura.json` quantos
   votos foram realocados.

Saída: dados/intermediario/votos_regiao.parquet
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes


def projetar(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta x/y em metros (EPSG:5880).

    Distância tem que ser calculada em metros: um grau de longitude vale ~111 km
    no norte do país e ~78 km no sul, então "vizinho mais próximo" medido em
    graus é uma resposta diferente — e enviesada por latitude.
    """
    transformador = Transformer.from_crs(config.CRS_GEOGRAFICO, config.CRS_METRICO, always_xy=True)
    df = df.copy()
    df["x"], df["y"] = transformador.transform(df["longitude_final"].values, df["latitude_final"].values)
    return df


def juntar_votos_regiao(votos: pd.DataFrame, de_para: pd.DataFrame) -> pd.DataFrame:
    total_antes = int(votos["qt_votos"].sum())
    juntado = votos.merge(de_para, on="id_local_votacao", how="inner")

    # Junção fraca aqui é sintoma de chave malformada, não de dado ausente — a
    # cobertura esperada é a da geocodificação (~84%), nunca perto de zero.
    validacoes.checar_taxa_de_juncao(
        len(juntado), len(votos), "votos × regiões", minimo=0.5
    )

    perdidos = total_antes - int(juntado["qt_votos"].sum())
    print(
        f"  votos em local geocodificado: {int(juntado['qt_votos'].sum()):,} de {total_antes:,} "
        f"({juntado['qt_votos'].sum() / total_antes * 100:.1f}%)"
    )
    print(f"  votos em local sem coordenada (ficam de fora): {perdidos:,}")
    return juntado


def regioes_ativas_na_referencia(votos: pd.DataFrame) -> set[int]:
    ativo = votos[votos["ano_eleicao"] == config.ANO_REFERENCIA_MALHA]["id_regiao"].unique()
    print(f"  regiões ativas em {config.ANO_REFERENCIA_MALHA}: {len(ativo):,}")
    return set(ativo)


def mapear_para_referencia(dim: pd.DataFrame, ativas: set[int]) -> dict[int, int]:
    """Cada região inativa aponta para a região ativa mais próxima do mesmo município.

    Região ativa aponta para si mesma. Município sem nenhuma região ativa (o
    local fechou e nada abriu no lugar) mantém as próprias regiões como
    referência — melhor manter o voto visível numa área aproximada do que
    descartá-lo.
    """
    dim = projetar(dim)
    mapa: dict[int, int] = {r: r for r in ativas}
    sem_ativa_no_municipio = 0
    realocadas = 0

    for _, grupo in dim.groupby("cd_municipio_ibge", sort=False):
        alvos = grupo[grupo["id_regiao"].isin(ativas)]
        inativas = grupo[~grupo["id_regiao"].isin(ativas)]
        if inativas.empty:
            continue
        if alvos.empty:
            # Nenhuma referência neste município: as inativas viram referência.
            for rid in inativas["id_regiao"]:
                mapa[rid] = rid
            sem_ativa_no_municipio += len(inativas)
            continue

        arvore = cKDTree(alvos[["x", "y"]].values)
        _, indices = arvore.query(inativas[["x", "y"]].values, k=1)
        for rid, idx in zip(inativas["id_regiao"].values, indices):
            mapa[rid] = int(alvos.iloc[idx]["id_regiao"])
        realocadas += len(inativas)

    print(f"  regiões realocadas para a malha de referência: {realocadas:,}")
    if sem_ativa_no_municipio:
        print(
            f"  regiões em município sem nenhuma região de referência: {sem_ativa_no_municipio:,} "
            "(mantidas como estão)"
        )
    return mapa


def main() -> None:
    print("=== 22 · votos por região ===")
    config.garantir_pastas()

    votos = pd.read_parquet(config.DIR_INTERMEDIARIO / "votos_local_votacao.parquet")
    de_para = pd.read_parquet(config.DIR_INTERMEDIARIO / "de_para_local_regiao.parquet")
    dim = pd.read_parquet(config.DIR_INTERMEDIARIO / "dim_regiao.parquet")

    votos = juntar_votos_regiao(votos, de_para)
    ativas = regioes_ativas_na_referencia(votos)
    mapa = mapear_para_referencia(dim, ativas)

    total_antes = int(votos["qt_votos"].sum())
    votos["id_regiao"] = votos["id_regiao"].map(mapa).astype("int64")

    chave = ["id_regiao", "sg_uf", "ano_eleicao", "turno", "cargo",
             "sq_candidato", "nr_votavel", "nm_votavel", "sg_partido", "tipo_voto"]
    agregado = votos.groupby(chave, dropna=False, as_index=False)["qt_votos"].sum()

    # A realocação move voto de região, mas não pode criar nem destruir voto.
    validacoes.checar_soma_preservada(
        total_antes, agregado["qt_votos"].sum(), "realocação para a malha de referência"
    )
    validacoes.checar_chave_unica(agregado, chave, "votos_regiao")

    saida = config.DIR_INTERMEDIARIO / "votos_regiao.parquet"
    agregado.to_parquet(saida, index=False)

    print()
    print(f"  linhas: {len(agregado):,} | votos: {agregado['qt_votos'].sum():,}")
    print(f"  regiões com voto: {agregado['id_regiao'].nunique():,}")
    print(f"  salvo: {saida}")


if __name__ == "__main__":
    main()

"""Compara a nossa coordenada com a oficial do TSE, local a local.

A malha usa a coordenada geocodificada contra o CNEFE onde ela existe (ver
`dados_importados/PROVENIENCIA.md`) e a oficial no resto. As duas discordam numa
minoria de casos, e essa minoria tem efeito grande: medido em quatro municípios,
de 57% a 90% das mudanças de atribuição vêm de pontos deslocados mais de 500 m.

Este relatório é a fila de revisão. Para cada local presente nas duas fontes ele
mede a distância entre os pontos e, quando ela é grande, diz qual dos dois foge
da nuvem de pontos do próprio município — que é o indício de qual está errado.

Saída: publicado/divergencia_coordenadas.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

RAIO_TERRA_M = 6_371_000.0

# Acima disso, não é imprecisão de cadastro: é outro lugar.
LIMITE_DIVERGENCIA_M = 5_000.0

# Quantas vezes mais longe do centro do município um ponto precisa estar que o
# outro para ser apontado como o suspeito.
FATOR_SUSPEITO = 3.0

PIORES = 50


def distancia_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    seno = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * RAIO_TERRA_M * np.arcsin(np.sqrt(seno))


def carregar() -> pd.DataFrame:
    dim = pd.read_parquet(config.DIR_INTERMEDIARIO / "dim_regiao.parquet")
    de_para = pd.read_parquet(config.DIR_INTERMEDIARIO / "de_para_local_regiao.parquet")
    oficiais = pd.read_parquet(config.DIR_INTERMEDIARIO / "locais_oficiais.parquet")

    nosso = de_para.merge(
        dim[["id_regiao", "latitude_final", "longitude_final", "cd_municipio_ibge",
             "nm_municipio", "sg_uf", "origem_coordenada"]],
        on="id_regiao", how="inner",
    )
    # Só faz sentido comparar onde a nossa coordenada é independente da do TSE.
    nosso = nosso[nosso["origem_coordenada"] == "cnefe"]
    return nosso.merge(
        oficiais[["id_local_votacao", "latitude", "longitude", "nm_local_votacao"]],
        on="id_local_votacao", how="inner",
    )


def medir(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["distancia_m"] = distancia_m(
        df["latitude_final"], df["longitude_final"], df["latitude"], df["longitude"]
    )
    # Centro do município segundo cada fonte, pela mediana dos seus próprios pontos.
    for fonte, (lat, lon) in {
        "nosso": ("latitude_final", "longitude_final"),
        "tse": ("latitude", "longitude"),
    }.items():
        centro = df.groupby("cd_municipio_ibge")[[lat, lon]].transform("median")
        df[f"ao_centro_{fonte}"] = distancia_m(df[lat], df[lon], centro.iloc[:, 0], centro.iloc[:, 1])
    return df


def suspeito(linha) -> str:
    if linha.ao_centro_tse > linha.ao_centro_nosso * FATOR_SUSPEITO:
        return "tse"
    if linha.ao_centro_nosso > linha.ao_centro_tse * FATOR_SUSPEITO:
        return "nosso"
    return "indefinido"


def main() -> None:
    print("=== divergência entre a nossa coordenada e a do TSE ===")
    df = medir(carregar())
    divergentes = df[df["distancia_m"] > LIMITE_DIVERGENCIA_M].copy()
    divergentes["suspeito"] = [suspeito(l) for l in divergentes.itertuples()]

    percentis = {f"p{p}": round(float(np.percentile(df["distancia_m"], p)), 1)
                 for p in (50, 75, 90, 95, 99)}
    contagem = divergentes["suspeito"].value_counts().to_dict()

    print(f"  locais comparáveis: {len(df):,}")
    print(f"  distância entre as fontes: " + " | ".join(f"{k} {v:,.0f} m" for k, v in percentis.items()))
    print(f"  acima de {LIMITE_DIVERGENCIA_M:,.0f} m: {len(divergentes):,} "
          f"({len(divergentes) / len(df) * 100:.1f}%)")
    for fonte in ("tse", "nosso", "indefinido"):
        print(f"     ponto suspeito — {fonte}: {contagem.get(fonte, 0):,}")

    piores = divergentes.nlargest(PIORES, "distancia_m")
    relatorio = {
        "locais_comparados": int(len(df)),
        "percentis_distancia_m": percentis,
        "limite_divergencia_m": LIMITE_DIVERGENCIA_M,
        "divergentes": int(len(divergentes)),
        "suspeito": {k: int(v) for k, v in contagem.items()},
        "piores": [
            {
                "id_local_votacao": l.id_local_votacao,
                "municipio": f"{l.nm_municipio} ({l.sg_uf})",
                "local": l.nm_local_votacao,
                "distancia_km": round(l.distancia_m / 1000, 1),
                "suspeito": l.suspeito,
            }
            for l in piores.itertuples()
        ],
    }
    destino = config.DIR_PUBLICADO / "divergencia_coordenadas.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"\n  salvo: {destino}")


if __name__ == "__main__":
    main()

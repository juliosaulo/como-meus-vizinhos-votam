"""Passo 11 — monta a dimensão de REGIÃO a partir dos locais de votação geocodificados.

Uma "região" é a área atendida por um local de votação. Como vários locais de
votação (zonas e seções diferentes) podem funcionar no mesmo prédio — e portanto
na mesma coordenada —, a região é definida pela **coordenada**, não pelo local:
locais que dividem a mesma coordenada são a mesma região.

Isso importa porque as duas metades do dado reagem de forma oposta a essa
duplicidade: o **voto** de cada local é distinto (urnas e eleitores distintos) e
tem que ser somado; o **atributo do lugar** é o mesmo e tem que ser deduplicado.
Errar o lado infla população ou some com eleitor.

Entrada:  dados_importados/locais_votacao_2018_2022.parquet
Saídas:   dados/intermediario/dim_regiao.parquet
          dados/intermediario/de_para_local_regiao.parquet
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes


def carregar_locais() -> pd.DataFrame:
    if not config.ARQ_LOCAIS_GEOCODIFICADOS.exists():
        raise FileNotFoundError(
            f"artefato importado não encontrado em {config.ARQ_LOCAIS_GEOCODIFICADOS}\n"
            "Ver dados_importados/PROVENIENCIA.md."
        )
    locais = pd.read_parquet(config.ARQ_LOCAIS_GEOCODIFICADOS)
    print(f"  locais no artefato importado: {len(locais):,}")
    return locais


def filtrar(locais: pd.DataFrame) -> pd.DataFrame:
    """Mantém só o que pode virar região: coordenada conhecida, dentro do
    território nacional e nas UFs desta execução."""
    # 'ZZ' é voto no exterior (Boston, Tóquio, Bruxelas) — não tem endereço
    # correspondente no CNEFE e está fora do escopo por construção.
    locais = locais[locais["sg_uf"] != "ZZ"]

    com_coordenada = locais["latitude_final"].notna() & locais["longitude_final"].notna()
    print(
        f"  com coordenada: {int(com_coordenada.sum()):,} "
        f"({com_coordenada.mean() * 100:.1f}%) — os demais não tinham nome/endereço "
        "aproveitável no TSE (ver PROVENIENCIA.md)"
    )
    locais = locais[com_coordenada].copy()

    if config.UFS_ALVO:
        locais = locais[locais["sg_uf"].isin(config.UFS_ALVO)]
        print(f"  recorte {config.UFS_ALVO}: {len(locais):,} locais")

    locais["cd_municipio_ibge"] = locais["cd_municipio_ibge"].astype(str).str.strip()
    validacoes.checar_coordenadas_no_brasil(
        locais["latitude_final"], locais["longitude_final"], "locais geocodificados"
    )
    return locais


def montar_regioes(locais: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Agrupa por coordenada exata e numera as regiões."""
    chave = ["latitude_final", "longitude_final"]

    # Se município ou UF variassem dentro de uma mesma coordenada, a região
    # herdaria um município arbitrário — checado antes de agrupar.
    validacoes.checar_constante_por_grupo(
        locais, chave, ["cd_municipio_ibge", "sg_uf"], "agrupamento por coordenada"
    )

    regioes = (
        locais.groupby(chave, as_index=False)
        .agg(
            sg_uf=("sg_uf", "first"),
            cd_municipio_ibge=("cd_municipio_ibge", "first"),
            nm_municipio=("nm_municipio", "first"),
            n_locais_votacao=("id_local_votacao", "nunique"),
        )
        .sort_values(["sg_uf", "cd_municipio_ibge", "latitude_final", "longitude_final"])
        .reset_index(drop=True)
    )
    regioes.insert(0, "id_regiao", range(1, len(regioes) + 1))

    # Nome e endereço para exibir. Coordenada compartilhada nem sempre é o mesmo
    # prédio: 74,6% dos grupos de coordenada compartilhada têm endereços
    # DIFERENTES no cadastro — tipicamente localidades rurais distintas que
    # o CNEFE não distingue. Por isso a região guarda um representante
    # determinístico e mantém a lista dos demais, em vez de fingir que é um
    # lugar só.
    ordenado = locais.sort_values(
        ["latitude_final", "longitude_final", "nm_local_votacao_consolidado", "id_local_votacao"]
    )
    representante = ordenado.groupby(chave, as_index=False).agg(
        nm_local_votacao=("nm_local_votacao_consolidado", "first"),
        ds_endereco=("ds_local_votacao_endereco_consolidado", "first"),
    )
    outros = (
        ordenado.groupby(chave)["nm_local_votacao_consolidado"]
        .apply(lambda s: sorted(set(s))[1:])
        .rename("outros_locais_mesma_coordenada")
        .reset_index()
    )
    regioes = regioes.merge(representante, on=chave, how="left").merge(outros, on=chave, how="left")

    de_para = locais[["id_local_votacao", "latitude_final", "longitude_final"]].merge(
        regioes[["id_regiao", *chave]], on=chave, how="inner"
    )[["id_local_votacao", "id_regiao"]]

    return regioes, de_para


def relatar(regioes: pd.DataFrame, de_para: pd.DataFrame) -> None:
    compartilhadas = regioes[regioes["n_locais_votacao"] > 1]
    print()
    print(f"  regiões: {len(regioes):,}")
    print(f"  locais mapeados: {len(de_para):,}")
    print(
        f"  regiões com mais de um local na mesma coordenada: {len(compartilhadas):,} "
        f"({len(compartilhadas) / len(regioes) * 100:.1f}%)"
    )
    print(f"  municípios cobertos: {regioes['cd_municipio_ibge'].nunique():,}")

    # Um local só pode pertencer a uma região — se isso quebrar, o voto seria
    # contado em duas regiões.
    validacoes.checar_chave_unica(de_para, ["id_local_votacao"], "de_para_local_regiao")


def main() -> None:
    print("=== 11 · dimensão de regiões ===")
    config.garantir_pastas()

    locais = carregar_locais()
    locais = filtrar(locais)
    regioes, de_para = montar_regioes(locais)
    relatar(regioes, de_para)

    saida_dim = config.DIR_INTERMEDIARIO / "dim_regiao.parquet"
    saida_depara = config.DIR_INTERMEDIARIO / "de_para_local_regiao.parquet"
    regioes.to_parquet(saida_dim, index=False)
    de_para.to_parquet(saida_depara, index=False)
    print()
    print(f"  salvo: {saida_dim}")
    print(f"  salvo: {saida_depara}")


if __name__ == "__main__":
    main()

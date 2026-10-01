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

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes

RAIO_TERRA_M = 6_371_000.0

# Dois locais a essa distância são, quase sempre, o mesmo prédio: zonas
# diferentes que funcionam na mesma escola. Abaixo disso, a coordenada nova
# cede lugar à que já existe, para o site não mostrar dois lugares.
RAIO_MESMO_PREDIO_M = 50.0


def carregar_locais() -> pd.DataFrame:
    if not config.ARQ_LOCAIS_GEOCODIFICADOS.exists():
        raise FileNotFoundError(
            f"artefato importado não encontrado em {config.ARQ_LOCAIS_GEOCODIFICADOS}\n"
            "Ver dados_importados/PROVENIENCIA.md."
        )
    locais = pd.read_parquet(config.ARQ_LOCAIS_GEOCODIFICADOS)
    locais["origem_coordenada"] = np.where(locais["latitude_final"].notna(), "cnefe", None)
    print(f"  locais no artefato importado: {len(locais):,}")
    return locais


def completar_com_oficiais(locais: pd.DataFrame) -> pd.DataFrame:
    """Completa a malha com a base oficial do TSE (passo 10).

    O artefato geocodificado manda onde existe — a coordenada dele é um endereço
    do CNEFE, o mesmo cadastro contra o qual o passo 31 mede distância. A base
    oficial entra em dois buracos que o artefato não tem como preencher:

    - locais cuja geocodificação não chegou a uma coordenada (16% deles, que têm
      voto apurado e hoje ficam fora do site);
    - locais que só existem em eleições posteriores à do artefato.

    Local novo que cai a menos de `RAIO_MESMO_PREDIO` de uma região já existente
    passa a usar a coordenada dela: quase sempre é a mesma escola, e dois pontos
    a vinte metros virariam dois lugares diferentes no site.
    """
    caminho = config.DIR_INTERMEDIARIO / "locais_oficiais.parquet"
    if not caminho.exists():
        raise FileNotFoundError(f"rode o passo 10 antes: falta {caminho}")
    oficiais = pd.read_parquet(caminho).set_index("id_local_votacao")

    # O arquivo oficial traz o código do município do TSE; a nossa chave usa o
    # do IBGE. O de-para sai do próprio artefato, que carrega os dois.
    codigo_tse = locais["id_local_votacao"].str.split("_").str[1]
    de_para_municipio = (
        pd.DataFrame({"tse": locais["sg_uf"] + "_" + codigo_tse,
                      "ibge": locais["cd_municipio_ibge"].astype(str),
                      "nome": locais["nm_municipio"]})
        .drop_duplicates("tse").set_index("tse")
    )

    sem_coordenada = locais["latitude_final"].isna()
    alvo = locais.index[sem_coordenada]
    coords = oficiais.reindex(locais.loc[alvo, "id_local_votacao"])
    preenchidos = coords["latitude"].notna().to_numpy()
    locais.loc[alvo[preenchidos], "latitude_final"] = coords.loc[preenchidos, "latitude"].to_numpy()
    locais.loc[alvo[preenchidos], "longitude_final"] = coords.loc[preenchidos, "longitude"].to_numpy()
    locais.loc[alvo[preenchidos], "origem_coordenada"] = "tse"
    print(f"  locais sem coordenada no artefato: {int(sem_coordenada.sum()):,} "
          f"— o TSE resolveu {int(preenchidos.sum()):,}")

    # Locais que o artefato não conhece.
    novos = oficiais.loc[~oficiais.index.isin(set(locais["id_local_votacao"]))].reset_index()
    novos["chave_municipio"] = novos["sg_uf"] + "_" + novos["cd_municipio_tse"]
    conhecido = novos["chave_municipio"].isin(de_para_municipio.index)
    if not conhecido.all():
        fora = novos.loc[~conhecido, "chave_municipio"].nunique()
        print(f"  [aviso] {int((~conhecido).sum()):,} local(is) em {fora:,} município(s) sem "
              "equivalente IBGE no artefato — ficam de fora")
    novos = novos[conhecido]
    novos = novos.assign(
        cd_municipio_ibge=de_para_municipio.loc[novos["chave_municipio"], "ibge"].to_numpy(),
        nm_municipio=de_para_municipio.loc[novos["chave_municipio"], "nome"].to_numpy(),
        nm_local_votacao_consolidado=novos["nm_local_votacao"],
        ds_local_votacao_endereco_consolidado=novos["ds_endereco"],
        latitude_final=novos["latitude"],
        longitude_final=novos["longitude"],
        status_geocodificacao="oficial_tse",
        origem_coordenada="tse",
    )[locais.columns]

    novos = encostar_em_regiao_existente(novos, locais)
    print(f"  locais só na base oficial: {len(novos):,} acrescentados")
    return descartar_coordenada_ambigua(pd.concat([locais, novos], ignore_index=True))


def descartar_coordenada_ambigua(locais: pd.DataFrame) -> pd.DataFrame:
    """Devolve ao estado "sem coordenada" o ponto oficial que cairia em dois municípios.

    A região é definida pela coordenada, e isso só se sustenta enquanto cada
    coordenada pertencer a um município só. A base oficial repete o mesmo ponto
    em municípios diferentes num punhado de casos — adotá-lo tornaria o
    agrupamento ambíguo, então ele não é adotado.
    """
    chave = ["latitude_final", "longitude_final"]
    com_coordenada = locais[locais["latitude_final"].notna()]
    municipios_por_ponto = com_coordenada.groupby(chave)["cd_municipio_ibge"].nunique()
    ambiguos = municipios_por_ponto[municipios_por_ponto > 1].index
    if len(ambiguos) == 0:
        return locais

    conflitante = pd.MultiIndex.from_frame(locais[chave]).isin(ambiguos)
    descartar = conflitante & (locais["origem_coordenada"] == "tse")
    locais.loc[descartar, ["latitude_final", "longitude_final", "origem_coordenada"]] = None
    print(f"  coordenadas oficiais descartadas por cair em mais de um município: "
          f"{int(descartar.sum()):,} em {len(ambiguos):,} ponto(s)")
    return locais


def encostar_em_regiao_existente(novos: pd.DataFrame, locais: pd.DataFrame) -> pd.DataFrame:
    """Local novo a poucos metros de um ponto já conhecido herda a coordenada dele."""
    base = locais[locais["latitude_final"].notna()]
    encostados = 0
    for municipio, grupo in novos.groupby("cd_municipio_ibge"):
        vizinhos = base[base["cd_municipio_ibge"] == municipio]
        if vizinhos.empty:
            continue
        # Graus para metros, suficiente na escala de um município.
        lat0 = np.radians(float(vizinhos["latitude_final"].mean()))
        def metros(lat, lon):
            return np.c_[np.radians(lon) * RAIO_TERRA_M * np.cos(lat0), np.radians(lat) * RAIO_TERRA_M]

        arvore = cKDTree(metros(vizinhos["latitude_final"].to_numpy(), vizinhos["longitude_final"].to_numpy()))
        dist, indice = arvore.query(metros(grupo["latitude_final"].to_numpy(), grupo["longitude_final"].to_numpy()))
        perto = dist <= RAIO_MESMO_PREDIO_M
        if perto.any():
            alvo = grupo.index[perto]
            novos.loc[alvo, "latitude_final"] = vizinhos["latitude_final"].to_numpy()[indice[perto]]
            novos.loc[alvo, "longitude_final"] = vizinhos["longitude_final"].to_numpy()[indice[perto]]
            novos.loc[alvo, "origem_coordenada"] = "cnefe"
            encostados += int(perto.sum())
    if encostados:
        print(f"  locais novos encostados num prédio já conhecido (até {RAIO_MESMO_PREDIO_M} m): {encostados:,}")
    return novos


def filtrar(locais: pd.DataFrame) -> pd.DataFrame:
    """Mantém só o que pode virar região: coordenada conhecida, dentro do
    território nacional e nas UFs desta execução."""
    # 'ZZ' é voto no exterior (Boston, Tóquio, Bruxelas) — não tem endereço
    # correspondente no CNEFE e está fora do escopo por construção.
    locais = locais[locais["sg_uf"] != "ZZ"]

    com_coordenada = locais["latitude_final"].notna() & locais["longitude_final"].notna()
    print(
        f"  com coordenada: {int(com_coordenada.sum()):,} "
        f"({com_coordenada.mean() * 100:.1f}%) — os demais não foram resolvidos nem pela "
        "geocodificação (ver PROVENIENCIA.md) nem pela base oficial do TSE"
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
            # "cnefe" vence "tse" na ordem alfabética, que é a prioridade certa:
            # o ponto casado com o CNEFE manda quando o grupo tem os dois.
            origem_coordenada=("origem_coordenada", "min"),
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
    locais = completar_com_oficiais(locais)
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

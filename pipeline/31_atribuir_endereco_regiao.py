"""Passo 31 — liga cada endereço do CNEFE à sua região.

Este é o passo que responde à pergunta do site: dado um endereço, em que local
de votação essa pessoa provavelmente vota? A resposta é o **local de votação
mais próximo dentro do mesmo município**, consultado numa árvore de vizinho mais
próximo (`scipy.cKDTree`) montada por município.

A distância tem que ser medida em metros (EPSG:5880), nunca em graus.

Saída: dados/intermediario/enderecos_regiao/{ANO_REFERENCIA_MALHA}/{UF}.parquet
       (um por UF, com checkpoint — numa pasta por malha, ver config)
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes

TAMANHO_BLOCO = 400_000

COLUNAS_CNEFE = [
    "COD_UNICO_ENDERECO", "COD_MUNICIPIO", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR",
    "NOM_SEGLOGR", "NUM_ENDERECO", "LATITUDE", "LONGITUDE", "COD_ESPECIE",
    "DSC_LOCALIDADE",
]

# Espécies de domicílio no CNEFE: particular e coletivo. As demais são
# estabelecimentos, edificações em construção e afins — endereços reais, que
# entram no índice, mas que não devem pesar na pergunta "onde a maioria desta
# rua vota". Uma rua pode ser só comércio; nesse caso o passo 32 cai para a
# contagem total.
ESPECIES_DOMICILIO = {"1", "2"}

COLUNAS_SAIDA = [
    "cd_municipio_ibge", "logradouro", "bairro", "numero", "id_regiao",
    "n_enderecos", "n_domicilios",
]

# Resumo por região da distância até os endereços atendidos. `dist_min` é o que
# denuncia coordenada errada: um local real sempre tem endereços colados nele.
COLUNAS_DISTANCIA = [
    "id_regiao", "n_enderecos", "soma_dist", "dist_min", "dist_max", "acima_5km", "acima_25km",
]

CODIGO_IBGE_POR_UF = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27,
    "SE": 28, "BA": 29, "MG": 31, "ES": 32, "RJ": 33, "SP": 35, "PR": 41,
    "SC": 42, "RS": 43, "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}


def arquivo_cnefe(uf: str) -> Path:
    return config.DIR_BRUTO_CNEFE / f"{CODIGO_IBGE_POR_UF[uf]}_{uf}.zip"


def montar_logradouro(df: pd.DataFrame) -> pd.Series:
    """Nome de rua legível a partir das três partes que o CNEFE guarda separadas.

    `NOM_TITULO_SEGLOGR` é o título honorífico ("DOUTOR", "PRESIDENTE") e vem
    vazio na maioria dos casos — daí o preenchimento antes de concatenar.
    """
    partes = [
        df["NOM_TIPO_SEGLOGR"].fillna("").str.strip(),
        df["NOM_TITULO_SEGLOGR"].fillna("").str.strip(),
        df["NOM_SEGLOGR"].fillna("").str.strip(),
    ]
    return (
        partes[0].str.cat(partes[1], sep=" ").str.cat(partes[2], sep=" ")
        .str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
    )


def arvores_por_municipio(regioes: pd.DataFrame) -> dict[str, tuple[cKDTree, list[int]]]:
    """Uma árvore de vizinho mais próximo por município.

    A busca é restrita ao município porque o eleitor vota no município onde tem
    domicílio eleitoral — sem essa restrição, um endereço perto da divisa seria
    atribuído a um local de votação de outra cidade.
    """
    arvores = {}
    for cd_municipio, grupo in regioes.groupby("cd_municipio_ibge", sort=False):
        arvores[cd_municipio] = (
            cKDTree(grupo[["x", "y"]].values),
            grupo["id_regiao"].tolist(),
        )
    return arvores


def carregar_regioes_alvo() -> pd.DataFrame:
    """Regiões que podem ser exibidas: as que têm voto depois da realocação."""
    dim = pd.read_parquet(config.DIR_INTERMEDIARIO / "dim_regiao.parquet")
    votos = pd.read_parquet(config.DIR_INTERMEDIARIO / "votos_regiao.parquet", columns=["id_regiao"])
    alvo = dim[dim["id_regiao"].isin(set(votos["id_regiao"]))].copy()

    transformador = Transformer.from_crs(config.CRS_GEOGRAFICO, config.CRS_METRICO, always_xy=True)
    alvo["x"], alvo["y"] = transformador.transform(
        alvo["longitude_final"].values, alvo["latitude_final"].values
    )
    validacoes.checar_crs_metrico(config.CRS_METRICO, "árvore de vizinho mais próximo")
    print(f"  regiões alvo (com voto): {len(alvo):,} em {alvo['cd_municipio_ibge'].nunique():,} município(s)")
    return alvo


def checkpoint_valido(destino: Path, regioes_uf: pd.DataFrame) -> bool:
    """O checkpoint só vale para a mesma numeração de regiões.

    `id_regiao` é numerado em sequência sobre o recorte de UFs: rodar o piloto
    de uma UF e depois o país inteiro renumera as regiões, e o checkpoint antigo
    passaria a apontar para regiões de outro município — sem erro nenhum. Quase
    passou despercebido na primeira execução nacional. A conferência é barata:
    cada região do checkpoint tem que existir na malha atual, no mesmo município.
    """
    # Checkpoint de uma versão anterior do passo (sem bairro, sem domicílios)
    # também precisa ser refeito — daí a conferência de colunas, que lê só o
    # cabeçalho do parquet.
    if not set(COLUNAS_SAIDA).issubset(pq.ParquetFile(destino).schema.names):
        return False
    salvo = pd.read_parquet(destino, columns=["cd_municipio_ibge", "id_regiao"]).drop_duplicates()
    municipio_atual = dict(zip(regioes_uf["id_regiao"], regioes_uf["cd_municipio_ibge"]))
    return bool((salvo["id_regiao"].map(municipio_atual) == salvo["cd_municipio_ibge"]).all())


def processar_uf(uf: str, regioes: pd.DataFrame,
                 transformador: Transformer) -> tuple[pd.DataFrame, pd.DataFrame]:
    caminho = arquivo_cnefe(uf)
    if not caminho.exists():
        raise FileNotFoundError(f"CNEFE de {uf} não encontrado em {caminho}")

    regioes_uf = regioes[regioes["sg_uf"] == uf]
    arvores = arvores_por_municipio(regioes_uf)

    partes: list[pd.DataFrame] = []
    partes_dist: list[pd.DataFrame] = []
    lidos = 0
    sem_municipio = 0
    sem_coordenada = 0

    with zipfile.ZipFile(caminho) as z, z.open(z.namelist()[0]) as f:
        for bloco in pd.read_csv(
            f, sep=";", encoding="latin1", dtype=str,
            usecols=COLUNAS_CNEFE, chunksize=TAMANHO_BLOCO,
        ):
            lidos += len(bloco)

            # (id_cnefe, cod_especie) é a chave do CNEFE: o código de endereço
            # sozinho não é único — o mesmo código serve um domicílio e um
            # estabelecimento no mesmo lote.
            bloco = bloco.drop_duplicates(subset=["COD_UNICO_ENDERECO", "COD_ESPECIE"])

            bloco["latitude"] = pd.to_numeric(bloco["LATITUDE"], errors="coerce")
            bloco["longitude"] = pd.to_numeric(bloco["LONGITUDE"], errors="coerce")
            valido = bloco["latitude"].notna() & bloco["longitude"].notna()
            sem_coordenada += int((~valido).sum())
            bloco = bloco[valido]
            if bloco.empty:
                continue

            bloco["cd_municipio_ibge"] = bloco["COD_MUNICIPIO"].str.strip()
            bloco["logradouro"] = montar_logradouro(bloco)
            bloco["numero"] = pd.to_numeric(bloco["NUM_ENDERECO"], errors="coerce")
            # DSC_LOCALIDADE é a "localidade" do IBGE: em cidade costuma ser o
            # bairro, no interior é a localidade rural (LINHA 617, ABUNA).
            bloco["bairro"] = bloco["DSC_LOCALIDADE"].fillna("").str.strip().str.upper()
            bloco["domicilio"] = bloco["COD_ESPECIE"].isin(ESPECIES_DOMICILIO)
            bloco["x"], bloco["y"] = transformador.transform(
                bloco["longitude"].values, bloco["latitude"].values
            )

            for cd_municipio, grupo in bloco.groupby("cd_municipio_ibge", sort=False):
                arvore = arvores.get(cd_municipio)
                if arvore is None:
                    sem_municipio += len(grupo)
                    continue
                tree, ids = arvore
                distancias, indices = tree.query(grupo[["x", "y"]].values, k=1)
                atribuido = grupo[["cd_municipio_ibge", "logradouro", "bairro", "numero", "domicilio"]].copy()
                atribuido["id_regiao"] = [ids[i] for i in indices]

                # A árvore já devolve a distância; guardá-la por região custa
                # quase nada e é o único sinal que denuncia local de votação com
                # coordenada errada — ele continuaria sendo "o mais próximo" de
                # alguém, só que a quilômetros de distância.
                dist = pd.DataFrame({"id_regiao": atribuido["id_regiao"].values, "d": distancias})
                dist["acima_5km"] = dist["d"] > 5_000
                dist["acima_25km"] = dist["d"] > 25_000
                partes_dist.append(
                    dist.groupby("id_regiao", as_index=False).agg(
                        n_enderecos=("d", "size"), soma_dist=("d", "sum"),
                        dist_min=("d", "min"), dist_max=("d", "max"),
                        acima_5km=("acima_5km", "sum"), acima_25km=("acima_25km", "sum"),
                    )
                )
                # Conta os endereços em vez de deduplicar: quando o mesmo número
                # de uma rua cai em duas regiões (lados opostos da via, quadra
                # na divisa), o passo 32 precisa saber qual delas tem mais
                # endereços para desempatar. Deduplicar aqui apagaria esse peso
                # e transformaria o desempate num sorteio.
                partes.append(
                    atribuido.groupby(
                        ["cd_municipio_ibge", "logradouro", "bairro", "numero", "id_regiao"],
                        dropna=False, as_index=False,
                    ).agg(n_enderecos=("domicilio", "size"), n_domicilios=("domicilio", "sum"))
                )

    if not partes:
        raise RuntimeError(f"{uf}: nenhum endereço atribuído")

    resultado = (
        pd.concat(partes, ignore_index=True)
        .groupby(
            ["cd_municipio_ibge", "logradouro", "bairro", "numero", "id_regiao"],
            dropna=False, as_index=False,
        )[["n_enderecos", "n_domicilios"]]
        .sum()
    )[COLUNAS_SAIDA]
    distancias = (
        pd.concat(partes_dist, ignore_index=True)
        .groupby("id_regiao", as_index=False)
        .agg(n_enderecos=("n_enderecos", "sum"), soma_dist=("soma_dist", "sum"),
             dist_min=("dist_min", "min"), dist_max=("dist_max", "max"),
             acima_5km=("acima_5km", "sum"), acima_25km=("acima_25km", "sum"))
    )

    atribuidos = lidos - sem_municipio - sem_coordenada
    print(
        f"    {uf}: {lidos:,} endereços lidos | {atribuidos:,} atribuídos "
        f"({atribuidos / lidos * 100:.1f}%) | {len(resultado):,} combinações rua×número×região"
    )
    if sem_municipio:
        print(
            f"    {uf}: {sem_municipio:,} endereço(s) em município sem local de votação "
            "geocodificado — sem região atribuível"
        )
    return resultado, distancias


def main() -> None:
    print("=== 31 · endereço → região (vizinho mais próximo) ===")
    config.garantir_pastas()
    saida_dir = config.DIR_ENDERECOS_REGIAO
    saida_dist = config.DIR_DISTANCIAS_REGIAO
    saida_dir.mkdir(parents=True, exist_ok=True)
    saida_dist.mkdir(parents=True, exist_ok=True)
    print(f"  malha de referência: {config.ANO_REFERENCIA_MALHA} ({saida_dir})")

    regioes = carregar_regioes_alvo()
    transformador = Transformer.from_crs(config.CRS_GEOGRAFICO, config.CRS_METRICO, always_xy=True)

    for uf in config.ufs_para_processar():
        destino = saida_dir / f"{uf}.parquet"
        destino_dist = saida_dist / f"{uf}.parquet"
        dist_completa = destino_dist.exists() and set(COLUNAS_DISTANCIA).issubset(
            pq.ParquetFile(destino_dist).schema.names
        )
        if destino.exists() and dist_completa:
            if checkpoint_valido(destino, regioes[regioes["sg_uf"] == uf]):
                print(f"  {uf}: já processado, pulando (apague o arquivo para refazer)")
                continue
            print(f"  {uf}: checkpoint feito com outra numeração de regiões — refazendo")
        print(f"  -- {uf} --")
        resultado, distancias = processar_uf(uf, regioes, transformador)
        resultado.to_parquet(destino, index=False)
        distancias.to_parquet(destino_dist, index=False)
        print(f"    salvo: {destino}")


if __name__ == "__main__":
    main()

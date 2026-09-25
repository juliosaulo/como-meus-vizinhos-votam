"""Passo 21 — lê a votação por seção do TSE e agrega por local de votação.

O TSE não publica "votos por partido por local de votação" pronto. O que existe
é um registro por seção e por votável, sem o partido do candidato nominal — o
partido só existe no cadastro de candidatos, que é outro arquivo, ligado pelo
sequencial do candidato.

Duas particularidades do formato que custaram caro para descobrir e estão
tratadas aqui:

- **Presidente vem num arquivo à parte.** No TSE, "Eleição Geral Federal"
  (Presidente) e "Eleições Gerais Estaduais" (os demais cargos) são registros de
  eleição distintos, com downloads distintos, mesmo ocorrendo no mesmo dia e na
  mesma urna. Presidente é um arquivo nacional único; Deputado Federal vem num
  arquivo por UF.
- **Eleição suplementar vem empilhada** sob o mesmo ano, e precisa ser
  descartada (ver `qualidade/validacoes.filtrar_eleicao_ordinaria`).

Como o arquivo de Presidente é nacional, o total apurado é conferido contra o
resultado oficial divulgado **mesmo quando a execução é de uma UF só** — é a
validação mais forte do pipeline e não custa nada manter ligada num recorte pequeno.

Saída: dados/intermediario/votos_local_votacao.parquet
"""

from __future__ import annotations

import sys
import unicodedata
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes

TAMANHO_BLOCO = 500_000

COLUNAS = [
    "ANO_ELEICAO", "NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NR_ZONA", "NR_SECAO",
    "DS_CARGO", "NR_VOTAVEL", "NM_VOTAVEL", "QT_VOTOS", "NR_LOCAL_VOTACAO",
    "SQ_CANDIDATO", "CD_TIPO_ELEICAO",
]

CHAVE_AGREGACAO = [
    "id_local_votacao", "sg_uf", "ano_eleicao", "turno", "cargo",
    "sq_candidato", "nr_votavel", "nm_votavel", "sg_partido", "tipo_voto",
]


def sem_acento(texto: str) -> str:
    texto = str(texto).strip().upper()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def arquivos_do_cargo(ano: int, cargo: str) -> list[tuple[Path, str]]:
    """(zip, nome do csv dentro do zip) — nacional para Presidente, por UF para o resto."""
    if cargo == "PRESIDENTE":
        zip_nacional = config.DIR_BRUTO_VOTACAO_PRESIDENTE / f"votacao_secao_{ano}_BR.zip"
        return [(zip_nacional, f"votacao_secao_{ano}_BR.csv")]
    return [
        (config.DIR_BRUTO_VOTACAO_UF / str(ano) / f"votacao_secao_{ano}_{uf}.zip",
         f"votacao_secao_{ano}_{uf}.csv")
        for uf in config.ufs_para_processar()
    ]


def montar_id_local(df: pd.DataFrame) -> pd.Series:
    """`UF_MUNICIPIO_ZONA_LOCAL`, no formato do artefato importado (`RO_00019_1_1031`).

    Espera `CD_MUNICIPIO` já normalizado. O separador explícito é necessário:
    sem ele, "zona 1 + local 1031" e "zona 11 + local 031" dariam a mesma chave.
    """
    return (
        df["SG_UF"].str.strip() + "_"
        + df["CD_MUNICIPIO"] + "_"
        + df["NR_ZONA"].str.strip() + "_"
        + df["NR_LOCAL_VOTACAO"].str.strip()
    )


def classificar_tipo_voto(df: pd.DataFrame) -> pd.Series:
    """Separa voto nominal de legenda, branco, nulo e anulado.

    Usa os códigos oficiais do TSE: 95/96/97 no número do votável e o
    sequencial de candidato igual a -3 para voto de legenda.

    O voto de legenda nem sempre vem com -3. No arquivo de Deputado Federal do
    DF em 2018 ele vem com sequencial -1 (#NULO): 58 mil linhas e 86.806 votos
    que eram classificados como nominais sem partido — a guarda de partido
    derrubou a primeira execução nacional. Por isso também é legenda o
    sequencial -1 com número de 2 dígitos (número de partido). Nominal com
    sequencial vazio e 4 dígitos continua caindo na guarda.
    """
    legenda = (df["SQ_CANDIDATO"] == config.CODIGO_VOTO_LEGENDA) | (
        (df["SQ_CANDIDATO"] == config.SQ_CANDIDATO_NULO)
        & (df["NR_VOTAVEL"].str.strip().str.len() == 2)
    )
    condicoes = [
        df["NR_VOTAVEL"] == config.CODIGO_VOTO_BRANCO,
        df["NR_VOTAVEL"] == config.CODIGO_VOTO_NULO,
        df["NR_VOTAVEL"] == config.CODIGO_VOTO_ANULADO,
        legenda,
    ]
    return pd.Series(
        np.select(condicoes, ["branco", "nulo", "anulado", "legenda"], default="nominal"),
        index=df.index,
    )


def carregar_cadastro_candidatos(ano: int, cargo: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devolve (candidato → partido, número do partido → sigla).

    O segundo é necessário porque o voto de legenda não tem candidato: o
    votável é o próprio número do partido.
    """
    pasta = config.DIR_BRUTO_CANDIDATOS / str(ano)
    zips = [p for p in pasta.glob("consulta_cand_*.zip") if "complementar" not in p.name.lower()]
    if not zips:
        raise FileNotFoundError(f"cadastro de candidatos não encontrado em {pasta}")

    with zipfile.ZipFile(zips[0]) as z:
        nome_csv = next(n for n in z.namelist() if n.upper().endswith("_BRASIL.CSV"))
        with z.open(nome_csv) as f:
            cad = pd.read_csv(
                f, sep=";", encoding="latin1", dtype=str,
                usecols=["DS_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO", "NM_URNA_CANDIDATO",
                         "NR_PARTIDO", "SG_PARTIDO"],
            )

    partidos = cad[["NR_PARTIDO", "SG_PARTIDO"]].drop_duplicates("NR_PARTIDO")
    cad = cad[cad["DS_CARGO"].map(sem_acento) == sem_acento(cargo)]
    cad = cad.drop_duplicates("SQ_CANDIDATO")
    print(f"    cadastro: {len(cad):,} candidato(s) de {cargo}")
    return cad[["SQ_CANDIDATO", "NM_URNA_CANDIDATO", "SG_PARTIDO"]], partidos


def ler_e_agregar(ano: int, cargo: str) -> tuple[pd.DataFrame, pd.Series]:
    """Lê os arquivos do cargo em blocos e agrega por local.

    Devolve também o total nacional por votável (só faz sentido para
    Presidente, cujo arquivo é nacional) para a validação oficial.
    """
    cargo_norm = sem_acento(cargo)
    ufs_alvo = set(config.ufs_para_processar())
    blocos: list[pd.DataFrame] = []
    total_nacional: dict[tuple[str, str], int] = {}
    linhas_lidas = 0
    descartadas_suplementar = 0

    for caminho_zip, nome_csv in arquivos_do_cargo(ano, cargo):
        if not caminho_zip.exists():
            # Pular em silêncio publicaria o ano sem uma UF inteira.
            raise FileNotFoundError(f"arquivo de votação não encontrado: {caminho_zip}")
        with zipfile.ZipFile(caminho_zip) as z, z.open(nome_csv) as f:
            for bloco in pd.read_csv(
                f, sep=";", encoding="latin1", dtype=str,
                usecols=COLUNAS, chunksize=TAMANHO_BLOCO,
            ):
                linhas_lidas += len(bloco)
                bloco = bloco[bloco["DS_CARGO"].map(sem_acento) == cargo_norm]
                if bloco.empty:
                    continue

                antes = len(bloco)
                bloco = validacoes.filtrar_eleicao_ordinaria(bloco)
                descartadas_suplementar += antes - len(bloco)
                if bloco.empty:
                    continue

                bloco["QT_VOTOS"] = pd.to_numeric(bloco["QT_VOTOS"], errors="coerce").fillna(0).astype("int64")

                # Total nacional antes de qualquer recorte de UF — é o que
                # permite validar contra o resultado oficial mesmo com uma UF só.
                for (turno, nome), votos in bloco.groupby(["NR_TURNO", "NM_VOTAVEL"])["QT_VOTOS"].sum().items():
                    total_nacional[(turno, nome)] = total_nacional.get((turno, nome), 0) + int(votos)

                bloco = bloco[bloco["SG_UF"].isin(ufs_alvo)]
                if bloco.empty:
                    continue

                bloco["CD_MUNICIPIO"] = validacoes.normalizar_codigo_municipio(bloco["CD_MUNICIPIO"])
                bloco["id_local_votacao"] = montar_id_local(bloco)
                bloco["tipo_voto"] = classificar_tipo_voto(bloco)
                bloco["NM_VOTAVEL"] = validacoes.unificar_voto_branco(bloco["NM_VOTAVEL"])

                blocos.append(
                    bloco.groupby(
                        ["id_local_votacao", "SG_UF", "ANO_ELEICAO", "NR_TURNO",
                         "NR_VOTAVEL", "NM_VOTAVEL", "SQ_CANDIDATO", "tipo_voto"],
                        as_index=False,
                    )["QT_VOTOS"].sum()
                )
        print(f"    lido: {caminho_zip.name}")

    if not blocos:
        raise RuntimeError(f"nenhuma linha de {cargo} em {ano} — confira os arquivos em {config.DIR_BRUTO}")

    print(f"    linhas brutas: {linhas_lidas:,}")
    if descartadas_suplementar:
        print(f"    eleição suplementar descartada: {descartadas_suplementar:,} linha(s)")

    df = pd.concat(blocos, ignore_index=True)
    df = df.rename(columns={
        "SG_UF": "sg_uf", "ANO_ELEICAO": "ano_eleicao", "NR_TURNO": "turno",
        "NR_VOTAVEL": "nr_votavel", "NM_VOTAVEL": "nm_votavel",
        "SQ_CANDIDATO": "sq_candidato", "QT_VOTOS": "qt_votos",
    })
    df["cargo"] = cargo
    df["ano_eleicao"] = df["ano_eleicao"].astype(int)
    df["turno"] = validacoes.normalizar_turno(df["turno"])

    serie_nacional = pd.Series(total_nacional, dtype="int64")
    return df, serie_nacional


def anexar_partido(df: pd.DataFrame, ano: int, cargo: str) -> pd.DataFrame:
    cadastro, partidos = carregar_cadastro_candidatos(ano, cargo)

    df = df.merge(
        cadastro.rename(columns={"SQ_CANDIDATO": "sq_candidato"}), on="sq_candidato", how="left"
    )
    df["sg_partido"] = df["SG_PARTIDO"]

    # Voto de legenda: o votável é o número do partido, não um candidato.
    legenda = df["tipo_voto"] == "legenda"
    if legenda.any():
        mapa = dict(zip(partidos["NR_PARTIDO"], partidos["SG_PARTIDO"]))
        df.loc[legenda, "sg_partido"] = df.loc[legenda, "nr_votavel"].map(mapa)

    nominal_sem_partido = int(((df["tipo_voto"] == "nominal") & df["sg_partido"].isna()).sum())
    if nominal_sem_partido:
        raise validacoes.ValidacaoFalhou(
            f"{nominal_sem_partido:,} linha(s) de voto nominal sem partido — o cruzamento "
            "com o cadastro de candidatos ficou incompleto."
        )
    return df.drop(columns=["SG_PARTIDO", "NM_URNA_CANDIDATO"], errors="ignore")


def main() -> None:
    print("=== 21 · votação por local de votação ===")
    config.garantir_pastas()
    print(f"  UFs: {config.ufs_para_processar()}")

    resultados = []
    for ano in config.ANOS_ELEICAO:
        for cargo in config.CARGOS_ALVO:
            print(f"\n  -- {cargo} · {ano} --")
            df, nacional = ler_e_agregar(ano, cargo)
            df = anexar_partido(df, ano, cargo)

            if cargo == "PRESIDENTE":
                for turno in sorted({t for t, _ in nacional.index}):
                    do_turno = {nome: int(v) for (t, nome), v in nacional.items() if t == turno}
                    validacoes.validar_totais_oficiais(do_turno, ano, turno)

            validacoes.checar_codigo_municipio(
                df["id_local_votacao"].str.split("_").str[1], f"{cargo} {ano}"
            )
            print(f"    linhas agregadas: {len(df):,} | votos: {df['qt_votos'].sum():,}")
            resultados.append(df)

    final = pd.concat(resultados, ignore_index=True)[CHAVE_AGREGACAO + ["qt_votos"]]
    saida = config.DIR_INTERMEDIARIO / "votos_local_votacao.parquet"
    final.to_parquet(saida, index=False)

    print()
    print(f"  total: {len(final):,} linhas | {final['qt_votos'].sum():,} votos")
    print(f"  salvo: {saida}")


if __name__ == "__main__":
    main()

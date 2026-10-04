"""Dos Boletins de Urna para a tabela que o pipeline já consome.

O passo 22 do pipeline espera `votos_local_votacao`: uma linha por local de
votação, cargo e votável. É o que este script produz a partir dos BUs baixados,
com o mesmo esquema e as mesmas convenções do passo 21 — assim nada mais
precisa saber que 2026 veio por um caminho diferente.

    python coleta_2026/converter.py                       # tudo que estiver baixado
    python coleta_2026/converter.py --uf RR --eleicao 619 --cargo 11 13

Três pontos onde o BU e o pipeline falam línguas diferentes, e a tradução:

- **Cargo**: o BU usa o código numérico do TSE (1, 6); o pipeline usa o nome
  ("PRESIDENTE", "DEPUTADO FEDERAL").
- **Candidato**: o BU traz o *número* digitado na urna, não o sequencial. O nome
  e o partido saem do cadastro de candidatos, ligados por número + cargo +
  **unidade eleitoral** — que é "BR" para presidente, a UF para deputado federal
  e o município para prefeito e vereador. Ligar só por UF daria o candidato
  errado em eleição municipal, onde o mesmo número se repete entre municípios.
- **Branco, nulo e legenda**: o pipeline tem convenções próprias (nº 95 e 96,
  sequencial -1; legenda com sequencial -3), herdadas do formato do TSE em CSV.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bu

RAIZ = Path(__file__).resolve().parents[1]
PASTA_BU = RAIZ / "dados" / "bruto" / "bu"
PASTA_CANDIDATOS = RAIZ / "dados" / "bruto" / "candidatos"
CADASTRO_DE_LOCAIS = RAIZ / "dados" / "bruto" / "locais_oficiais"
DE_PARA = RAIZ / "dados" / "intermediario" / "de_para_local_regiao.parquet"
SAIDA = RAIZ / "dados" / "intermediario" / "votos_local_votacao_2026.parquet"
BASE_DO_PIPELINE = RAIZ / "dados" / "intermediario" / "votos_local_votacao.parquet"

# Códigos de cargo do TSE (guia de download, seção 5). O projeto publica só os
# dois primeiros, mas a tabela completa permite ensaiar com eleições passadas —
# foi com a de 2024, municipal, que este conversor foi conferido.
CARGOS = {
    1: "PRESIDENTE", 3: "GOVERNADOR", 5: "SENADOR", 6: "DEPUTADO FEDERAL",
    7: "DEPUTADO ESTADUAL", 8: "DEPUTADO DISTRITAL", 11: "PREFEITO", 13: "VEREADOR",
}

# Em que âmbito o número do candidato é único — a coluna SG_UE do cadastro.
UNIDADE_ELEITORAL = {
    1: "brasil",      # presidente: número nacional
    3: "uf", 5: "uf", 6: "uf", 7: "uf", 8: "uf",
    11: "municipio", 13: "municipio",
}

# Convenções do passo 21, para a tabela sair idêntica à dos outros anos.
NAO_NOMINAL = {
    "branco": ("95", "VOTO EM BRANCO", "-1"),
    "nulo": ("96", "VOTO NULO", "-1"),
    # Cargo sem candidato nenhum (TipoVoto 5 da especificação do TSE). Não pode
    # acontecer com presidente ou deputado federal numa eleição geral; se
    # acontecer, entra como nulo, que é o destino desses votos na totalização.
    "cargo_sem_candidato": ("96", "VOTO NULO", "-1"),
}
SQ_LEGENDA = "-3"


# Quanto do acervo pode ser recusado antes de a conversão parar. Recusa isolada
# é urna com arquivo corrompido; recusa em massa é formato diferente do esperado,
# e aí seguir seria publicar um resultado construído sobre leitura errada.
LIMITE_RECUSA = 0.005          # 0,5%
AMOSTRA_INICIAL = 200          # curto-circuito: se já começa errado, para logo
LIMITE_AMOSTRA_INICIAL = 0.5


def relatar_fora_da_malha(df: pd.DataFrame, caminho_de_para: Path | None = None) -> dict:
    """Quanto voto está em local que a malha do projeto não conhece.

    O boletim declara o local dele, e é ele que manda — é o dado primário,
    escrito pela urna. Quando esse local não está na malha, o voto é descartado
    na junção do passo 22, e esta função diz o tamanho da perda **antes** de três
    horas de pipeline.

    Não há conserto automático de propósito. O caminho óbvio — usar o local que o
    cadastro de locais atribui àquela seção — foi medido e recusado: no ensaio com
    Roraima em 2024, cadastro e boletim discordam do local em 6,5% das seções, e
    em nenhum dos casos de perda o cadastro apontava para um local que estivesse
    na malha. Mover voto para outro prédio com base numa fonte que discorda da
    urna faria o resultado parecer completo sem ser — pior que perder 0,3%.
    """
    caminho_de_para = caminho_de_para or DE_PARA
    if not caminho_de_para.exists():
        return {}
    na_malha = set(pd.read_parquet(caminho_de_para, columns=["id_local_votacao"])
                   ["id_local_votacao"])
    fora = df[~df["id_local_votacao"].isin(na_malha)]
    total = int(df["qt_votos"].sum())
    perdidos = int(fora["qt_votos"].sum())
    if perdidos:
        print(f"  [aviso] {perdidos:,} voto(s) ({perdidos / max(total, 1):.2%}) em "
              f"{fora['id_local_votacao'].nunique():,} local(is) fora da malha — "
              f"o passo 22 vai descartá-los")
    return {"votos_fora": perdidos, "locais_fora": int(fora["id_local_votacao"].nunique())}


def ler_bus(caminho_zip: Path, eleicao: int, cargos: set[int]) -> tuple[Counter, dict]:
    """Soma os votos de um zip de UF, por (município, zona, local, cargo, votável).

    BU que não encaixa na estrutura ou que não fecha a própria conta é recusado e
    contado, não interrompe o acervo. O que interrompe é a **taxa** de recusa:
    ver `LIMITE_RECUSA`.
    """
    contagem: Counter = Counter()
    resumo = {"bus": 0, "recusados": 0, "problemas": 0, "sem_eleicao": 0}
    motivos: list[str] = []

    def recusar(nome: str, motivo: str, chave: str) -> None:
        resumo[chave] += 1
        if len(motivos) < 5:
            motivos.append(f"{nome}: {motivo}")

    def conferir_taxa(total: int, limite: float) -> None:
        ruins = resumo["recusados"] + resumo["problemas"]
        if total and ruins / total > limite:
            raise SystemExit(
                f"\n{caminho_zip.name}: {ruins:,} de {total:,} boletins recusados "
                f"({ruins / total:.1%}), acima do limite de {limite:.1%}. "
                f"Isto é formato diferente do esperado, não urna com defeito — "
                f"a conversão para aqui.\n  " + "\n  ".join(motivos)
            )

    with zipfile.ZipFile(caminho_zip) as z:
        for nome in z.namelist():
            resumo["bus"] += 1
            if resumo["bus"] == AMOSTRA_INICIAL:
                conferir_taxa(AMOSTRA_INICIAL, LIMITE_AMOSTRA_INICIAL)
            try:
                boletim = bu.ler(z.read(nome))
            except bu.BoletimInvalido as erro:
                recusar(nome, str(erro), "recusados")
                continue
            if boletim.fase != "oficial":
                recusar(nome, f"fase {boletim.fase}, não oficial", "recusados")
                continue
            problemas = bu.conferir(boletim)
            if problemas:
                recusar(nome, problemas[0], "problemas")
                continue
            if eleicao not in boletim.eleicoes:
                resumo["sem_eleicao"] += 1
                continue
            for cargo in boletim.eleicoes[eleicao]:
                if cargo.codigo not in cargos:
                    continue
                for voto in cargo.votos:
                    chave = (boletim.municipio, boletim.zona, boletim.local, cargo.codigo,
                             voto.tipo, voto.partido, voto.numero)
                    contagem[chave] += voto.quantidade
    conferir_taxa(resumo["bus"], LIMITE_RECUSA)
    if motivos:
        print(f"    [aviso] {resumo['recusados']:,} recusado(s) por estrutura e "
              f"{resumo['problemas']:,} por não fechar a conta. Primeiros casos:")
        for motivo in motivos:
            print(f"      {motivo}")
    return contagem, resumo


def cadastro_de_candidatos(ano: int, pasta: Path | None = None) -> pd.DataFrame:
    """Número na urna → nome e partido, por cargo e UF.

    O número sozinho não identifica: deputado federal 1234 existe em várias UFs,
    e é outro candidato em cada uma.
    """
    pasta = pasta or PASTA_CANDIDATOS / str(ano)
    zips = [p for p in pasta.glob("consulta_cand_*.zip") if "complementar" not in p.name.lower()]
    if not zips:
        raise FileNotFoundError(f"cadastro de candidatos de {ano} não encontrado em {pasta}")

    partes = []
    with zipfile.ZipFile(zips[0]) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(".csv") or "BRASIL" in nome.upper():
                continue
            with z.open(nome) as f:
                partes.append(pd.read_csv(
                    io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str,
                    usecols=["SG_UF", "DS_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO",
                             "SG_UE", "NM_URNA_CANDIDATO", "NR_PARTIDO", "SG_PARTIDO",
                             "DS_SITUACAO_CANDIDATURA", "DS_SIT_TOT_TURNO"],
                ))
    cad = pd.concat(partes, ignore_index=True)
    cad["cargo"] = cad["DS_CARGO"].str.upper().str.strip()
    return escolher_candidatura(cad)


# Situações que significam "esta candidatura não é a que foi às urnas".
SITUACOES_SEM_VALOR = {"#NULO", "#NE", "", None}


def escolher_candidatura(cad: pd.DataFrame) -> pd.DataFrame:
    """Quando dois registros dividem o mesmo número, escolhe o que concorreu.

    Acontece em substituição de candidato: o cadastro guarda as duas inscrições
    com o mesmo número, e só uma recebeu voto. No ensaio com 2024, o número 44
    em Boa Vista tinha NICOLETTI (anulada) e CATARINA GUERRA (concorreu) — pegar
    a errada daria o nome errado em cima da contagem certa, que é o pior tipo de
    erro: silencioso.

    A ordem de preferência funciona antes e depois da totalização:
    situação de totalização real > candidatura apta > inscrição mais recente.
    """
    chave = ["cargo", "SG_UE", "NR_CANDIDATO"]
    repetidos = cad.duplicated(chave, keep=False)
    if not repetidos.any():
        return cad

    cad = cad.copy()
    cad["_tem_resultado"] = ~cad["DS_SIT_TOT_TURNO"].isin(SITUACOES_SEM_VALOR)
    cad["_apta"] = cad["DS_SITUACAO_CANDIDATURA"].str.upper().eq("APTO")
    cad = cad.sort_values(["_tem_resultado", "_apta", "SQ_CANDIDATO"],
                          ascending=[False, False, False])
    antes = len(cad)
    cad = cad.drop_duplicates(chave, keep="first")
    print(f"  números com mais de uma candidatura: {int(repetidos.sum()) - (antes - len(cad)):,} "
          f"mantida(s), {antes - len(cad):,} descartada(s)")
    return cad.drop(columns=["_tem_resultado", "_apta"])


def montar_tabela(contagem: Counter, uf: str, ano: int, turno: int,
                  cad: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for (municipio, zona, local, cargo, tipo, partido, numero), qtd in contagem.items():
        ambito = UNIDADE_ELEITORAL.get(cargo, "uf")
        unidade = {"brasil": "BR", "uf": uf.upper(), "municipio": municipio}[ambito]
        linhas.append({
            "id_local_votacao": f"{uf.upper()}_{municipio}_{zona}_{local}",
            "sg_uf": uf.upper(), "ano_eleicao": ano, "turno": str(turno),
            "cargo": CARGOS[cargo], "tipo_voto": tipo, "unidade_eleitoral": unidade,
            "nr_partido": None if partido is None else str(partido),
            "numero": None if numero is None else str(numero),
            "qt_votos": qtd,
        })
    df = pd.DataFrame(linhas)
    if df.empty:
        return df

    # Nominais: nome e partido vêm do cadastro, por número + cargo + unidade
    # eleitoral. O número sozinho não identifica ninguém.
    nominais = cad[["cargo", "SG_UE", "NR_CANDIDATO", "SQ_CANDIDATO",
                    "NM_URNA_CANDIDATO", "SG_PARTIDO"]]
    df = df.merge(nominais, how="left", left_on=["cargo", "unidade_eleitoral", "numero"],
                  right_on=["cargo", "SG_UE", "NR_CANDIDATO"])

    # Legenda: o número é o do partido; o nome é o do partido.
    partidos = cad[["NR_PARTIDO", "SG_PARTIDO"]].drop_duplicates("NR_PARTIDO").rename(
        columns={"SG_PARTIDO": "sigla_partido"})
    df = df.merge(partidos, how="left", left_on="nr_partido", right_on="NR_PARTIDO")

    df["nr_votavel"] = df["numero"]
    df["sq_candidato"] = df["SQ_CANDIDATO"]
    df["nm_votavel"] = df["NM_URNA_CANDIDATO"]
    df["sg_partido"] = df["SG_PARTIDO"]

    legenda = df["tipo_voto"] == "legenda"
    df.loc[legenda, "sq_candidato"] = SQ_LEGENDA
    df.loc[legenda, "sg_partido"] = df.loc[legenda, "sigla_partido"]
    df.loc[legenda, "nm_votavel"] = df.loc[legenda, "sigla_partido"]

    for tipo, (nr, nome, sq) in NAO_NOMINAL.items():
        alvo = df["tipo_voto"] == tipo
        df.loc[alvo, ["nr_votavel", "nm_votavel", "sq_candidato"]] = [nr, nome, sq]
        df.loc[alvo, "sg_partido"] = None

    return df[["id_local_votacao", "sg_uf", "ano_eleicao", "turno", "cargo", "sq_candidato",
               "nr_votavel", "nm_votavel", "sg_partido", "tipo_voto", "qt_votos"]]


def mesclar_na_base(df: pd.DataFrame, caminho: Path, ano: int) -> None:
    """Grava o ano convertido na tabela que o pipeline já lê.

    O conversor entrega o arquivo que o passo 22 consome, em vez de um formato
    novo: assim nenhum passo do pipeline precisa saber que este ano veio por
    outro caminho. A operação é idempotente — rodar de novo substitui as linhas
    do mesmo ano, em vez de duplicá-las.

    Atenção: rodar o passo 21 depois disto reescreve o arquivo a partir dos CSVs
    do TSE e leva junto o que foi mesclado. Basta rodar o conversor de novo — a
    saída própria (`votos_local_votacao_2026.parquet`) continua no lugar.
    """
    if not caminho.exists():
        df.to_parquet(caminho, index=False)
        print(f"  base criada: {caminho}")
        return

    base = pd.read_parquet(caminho)
    if list(base.columns) != list(df.columns):
        raise SystemExit(
            "esquema diferente do que o pipeline espera — a mesclagem corromperia a base. "
            f"base: {list(base.columns)} | conversão: {list(df.columns)}"
        )

    anteriores = int((base["ano_eleicao"] == ano).sum())
    base = base[base["ano_eleicao"] != ano]
    junto = pd.concat([base, df], ignore_index=True)
    junto.to_parquet(caminho, index=False)
    print(f"  mesclado em {caminho.name}: {len(df):,} linhas de {ano}"
          + (f", substituindo {anteriores:,} já existentes" if anteriores else "")
          + f" | tabela com {len(junto):,} linhas, anos "
            f"{sorted(int(a) for a in junto['ano_eleicao'].unique())}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ano", type=int, default=2026)
    parser.add_argument("--turno", type=int, default=1)
    parser.add_argument("--eleicao", type=int, help="código da eleição dentro do BU")
    parser.add_argument("--cargo", type=int, nargs="+", default=[bu.PRESIDENTE, bu.DEPUTADO_FEDERAL])
    parser.add_argument("--uf", nargs="+")
    parser.add_argument("--pasta", type=Path, default=PASTA_BU)
    parser.add_argument("--saida", type=Path, default=SAIDA)
    parser.add_argument("--candidatos", type=Path,
                        help="pasta do cadastro (padrão: dados/bruto/candidatos/<ano>)")
    parser.add_argument("--base", type=Path, default=BASE_DO_PIPELINE,
                        help="tabela que o passo 22 lê; o ano convertido é mesclado nela")
    parser.add_argument("--sem-mesclar", action="store_true",
                        help="só grava a saída própria, sem tocar na tabela do pipeline")
    args = parser.parse_args()

    if args.eleicao is None:
        sys.exit("informe --eleicao (o código que aparece dentro do BU; ver tse.eleicao_federal)")

    cad = cadastro_de_candidatos(args.ano, args.candidatos)
    print(f"=== conversão dos BUs — eleição {args.eleicao}, cargos {args.cargo}")
    print(f"  cadastro de candidatos: {len(cad):,} registros")

    zips = sorted(args.pasta.glob("*.zip"))
    if args.uf:
        alvo = {u.upper() for u in args.uf}
        zips = [z for z in zips if z.stem.upper() in alvo]
    if not zips:
        sys.exit(f"nenhum zip de BU em {args.pasta}")

    tabelas, totais = [], Counter()
    for caminho in zips:
        uf = caminho.stem
        contagem, resumo = ler_bus(caminho, args.eleicao, set(args.cargo))
        tabela = montar_tabela(contagem, uf, args.ano, args.turno, cad)
        tabelas.append(tabela)
        totais.update(resumo)
        ruins = resumo["recusados"] + resumo["problemas"]
        print(f"  {uf}: {resumo['bus']:,} BUs | {len(tabela):,} linhas | "
              f"{int(tabela['qt_votos'].sum()):,} votos"
              + (f" | {ruins:,} recusado(s)" if ruins else "")
              + (f" | {resumo['sem_eleicao']:,} sem a eleição pedida"
                 if resumo["sem_eleicao"] else ""))

    df = pd.concat(tabelas, ignore_index=True)
    relatar_fora_da_malha(df)
    sem_nome = int(df["nm_votavel"].isna().sum())
    if sem_nome:
        print(f"  [aviso] {sem_nome:,} linha(s) sem nome no cadastro — número não encontrado")

    args.saida.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.saida, index=False)
    print(f"\n  {len(df):,} linhas | {int(df['qt_votos'].sum()):,} votos")
    print(f"  salvo: {args.saida}")

    if not args.sem_mesclar:
        mesclar_na_base(df, args.base, args.ano)


if __name__ == "__main__":
    main()

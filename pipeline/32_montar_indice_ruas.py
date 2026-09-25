"""Passo 32 — comprime a atribuição por endereço num índice de rua consultável.

O passo anterior produz uma linha por combinação rua × bairro × número × região.
Isso responde a pergunta certa, mas é grande demais para servir a um site: no
país inteiro são dezenas de milhões de combinações.

A compressão explora o fato de que numeração de rua é **ordenada no espaço**:
percorrendo uma rua do número 1 em diante, a região muda poucas vezes. Então em
vez de guardar cada número, guarda-se apenas onde a região **muda**
(*run-length encoding*):

    Rua Dr. Brandão → [[1, 87], [520, 88], [1200, 87]]

Lê-se: do número 1 ao 519 é a região 87; de 520 a 1199 é a 88; de 1200 em diante
volta a ser a 87. Consultar é uma busca binária pelo maior início ≤ número
digitado. No Brasil isso leva 54,3 milhões de combinações a 6,5 milhões de trechos.

Por que a região "volta": ruas longas costumam atravessar a fronteira entre dois
locais de votação mais de uma vez, e os dois lados de uma mesma via podem cair
em regiões distintas. O índice não tenta suavizar isso — guarda o que a
geografia diz.

O número, porém, nem sempre existe (23,8% dos endereços são S/N) e nem sempre é
o que o usuário quer informar. Por isso o passo também resume a rua de três
outras formas, todas por **peso**: a região dominante, a lista de regiões da rua
e a mesma lista dentro de cada bairro. O bairro é o desempate de quem não tem ou
não quer dar o número, e é o único jeito de distinguir as vias homônimas que o
CNEFE empilha sob "RUA SEM DENOMINACAO".

**Peso** é o número de domicílios, não de endereços: a pergunta é onde mora
gente, e um endereço pode ser uma obra ou um comércio. Numa rua sem domicílio
nenhum (só comércio), o peso cai para a contagem de endereços, senão a rua
ficaria sem resposta.

Saídas: dados/intermediario/indice_ruas.parquet        (trechos por rua × número)
        dados/intermediario/ruas_dominante.parquet     (a região mais provável de cada rua)
        dados/intermediario/ruas_regioes.parquet       (ruas ambíguas: regiões com peso)
        dados/intermediario/ruas_bairro_regioes.parquet(ruas ambíguas: o mesmo, por bairro)
        dados/intermediario/bairros_regioes.parquet    (município × bairro: regiões com peso)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from qualidade import validacoes

RUA = ["cd_municipio_ibge", "logradouro"]


def carregar_enderecos(uf: str) -> pd.DataFrame:
    arquivo = config.DIR_ENDERECOS_REGIAO / f"{uf}.parquet"
    if not arquivo.exists():
        raise FileNotFoundError(f"rode o passo 31 primeiro — falta: {arquivo}")

    df = pd.read_parquet(arquivo)
    faltando = {"bairro", "n_domicilios"} - set(df.columns)
    if faltando:
        raise ValueError(
            f"{arquivo} é de uma versão anterior do passo 31 (faltam {sorted(faltando)}). "
            "Apague a pasta e rode o passo 31 de novo."
        )
    print(f"  combinações rua × bairro × número × região: {len(df):,}")
    return df


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Limpa e calcula o peso de cada linha.

    Peso é domicílio; numa rua que não tem nenhum (só comércio, só obra), cai
    para a contagem de endereços — do contrário a rua inteira pesaria zero e
    sumiria dos desempates.
    """
    df = df.dropna(subset=["logradouro", "id_regiao"]).copy()
    df = df[df["logradouro"] != ""]
    # Número 0 no CNEFE significa "sem número" (S/N). Mantido: é a única
    # informação de posição que existe para esses endereços.
    df["numero"] = df["numero"].fillna(0).astype("int64")
    df["bairro"] = df["bairro"].fillna("")

    tem_domicilio = df.groupby(RUA)["n_domicilios"].transform("sum") > 0
    df["peso"] = df["n_domicilios"].where(tem_domicilio, df["n_enderecos"])
    sem_dom = int((~tem_domicilio).sum())
    if sem_dom:
        print(f"  linhas em rua sem domicílio (peso = endereços): {sem_dom:,}")
    return df


def montar_trechos(df: pd.DataFrame) -> pd.DataFrame:
    """Run-length encoding da região ao longo da numeração de cada rua."""
    # Um mesmo número pode aparecer com mais de uma região (lados opostos da
    # via, condomínio na divisa). Fica a região com mais peso naquele número —
    # desempate por peso real, não por ordem de chegada.
    contagem = (
        df.groupby(RUA + ["numero", "id_regiao"], sort=False)["peso"].sum()
        .rename("n").reset_index()
    )
    contagem = contagem.sort_values(
        RUA + ["numero", "n", "id_regiao"],
        ascending=[True, True, True, False, True],
    )
    unico = contagem.drop_duplicates(subset=RUA + ["numero"], keep="first")

    rua = unico["cd_municipio_ibge"] + "|" + unico["logradouro"]
    inicio_de_trecho = unico["id_regiao"].ne(unico["id_regiao"].shift()) | rua.ne(rua.shift())

    trechos = unico[inicio_de_trecho][RUA + ["numero", "id_regiao"]].rename(
        columns={"numero": "numero_inicial"}
    )
    print(f"  trechos (rua × região contígua): {len(trechos):,} | ruas: {rua.nunique():,}")
    return trechos.reset_index(drop=True)


def regioes_com_fracao(df: pd.DataFrame, chave: list[str]) -> pd.DataFrame:
    """Regiões de cada chave, com a fração do peso que cada uma representa.

    Ordenada da região mais provável para a menos: é a ordem em que o site
    mostra as opções, e a primeira linha é a resposta quando não há número.
    """
    c = df.groupby(chave + ["id_regiao"], sort=False)[["peso", "n_enderecos"]].sum().reset_index()
    # O peso é domicílio, mas um grupo pode não ter nenhum — um bairro só de
    # comércio dentro de uma rua que tem casas noutro bairro. Sem esta queda
    # para a contagem de endereços, a fração sairia 0/0 e o JSON publicado
    # levaria `NaN`, que o JSON.parse do navegador recusa.
    total = c.groupby(chave)["peso"].transform("sum")
    c["peso"] = c["peso"].where(total > 0, c["n_enderecos"])
    c["fracao"] = c["peso"] / c.groupby(chave)["peso"].transform("sum")
    c["n_regioes"] = c.groupby(chave)["id_regiao"].transform("size")
    return c.sort_values(chave + ["peso", "id_regiao"], ascending=[True] * len(chave) + [False, True])


def regiao_dominante(por_rua: pd.DataFrame) -> pd.DataFrame:
    """A região mais provável de cada rua — a resposta quando não há número nem bairro."""
    dominante = por_rua.drop_duplicates(subset=RUA, keep="first")
    uma_regiao = (dominante["n_regioes"] == 1).mean()
    print(f"  ruas com uma região só (número dispensável): {uma_regiao * 100:.1f}%")
    print(
        "  confiança média quando a rua é ambígua: "
        f"{dominante.loc[dominante['n_regioes'] > 1, 'fracao'].mean() * 100:.1f}%"
    )
    return dominante.rename(columns={"id_regiao": "id_regiao_dominante"})[
        RUA + ["id_regiao_dominante", "n_regioes", "fracao"]
    ]


def main() -> None:
    print("=== 32 · índice de ruas ===")
    config.garantir_pastas()

    # Uma UF por vez: a rua é chave dentro do município, então o resultado é o
    # mesmo de processar tudo junto — e o país inteiro não cabe na memória.
    saidas: dict[str, list[pd.DataFrame]] = {
        "trechos": [], "dominante": [], "ruas_regioes": [],
        "ruas_bairro_regioes": [], "bairros_regioes": [],
    }
    for uf in config.ufs_para_processar():
        print(f"  -- {uf} --")
        df = preparar(carregar_enderecos(uf))

        saidas["trechos"].append(montar_trechos(df))

        por_rua = regioes_com_fracao(df, RUA)
        saidas["dominante"].append(regiao_dominante(por_rua))

        # Só as ruas ambíguas: nas de região única a lista seria de um item só,
        # e são 76% das ruas do país — publicá-las dobraria o arquivo à toa.
        ambiguas = por_rua[por_rua["n_regioes"] > 1]
        saidas["ruas_regioes"].append(ambiguas[RUA + ["id_regiao", "fracao", "peso"]])

        # O bairro só é publicado onde desempata: rua ambígua que passa por mais
        # de um bairro. Rua ambígua dentro de um bairro só cai direto na lista.
        chave_bairro = RUA + ["bairro"]
        com_bairro = df[df["bairro"] != ""]
        separaveis = com_bairro.merge(ambiguas[RUA].drop_duplicates(), on=RUA, how="inner")
        separaveis = separaveis[separaveis.groupby(RUA)["bairro"].transform("nunique") > 1]
        if not separaveis.empty:
            por_bairro = regioes_com_fracao(separaveis, chave_bairro)
            saidas["ruas_bairro_regioes"].append(
                por_bairro[chave_bairro + ["id_regiao", "fracao", "peso"]]
            )

        # Índice município × bairro, para quem não sabe o nome da rua.
        chave_mun_bairro = ["cd_municipio_ibge", "bairro"]
        por_mun_bairro = regioes_com_fracao(com_bairro, chave_mun_bairro)
        saidas["bairros_regioes"].append(
            por_mun_bairro[chave_mun_bairro + ["id_regiao", "fracao", "peso"]]
        )
        del df, por_rua

    final = {nome: pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
             for nome, partes in saidas.items()}

    print(
        f"\n  total: {len(final['trechos']):,} trechos | {len(final['dominante']):,} ruas | "
        f"{final['ruas_regioes'][RUA].drop_duplicates().shape[0]:,} ruas ambíguas | "
        f"{len(final['bairros_regioes']):,} linhas de bairro × região"
    )

    validacoes.checar_chave_unica(
        final["trechos"], RUA + ["numero_inicial"], "índice de ruas"
    )
    validacoes.checar_chave_unica(final["dominante"], RUA, "região dominante por rua")
    validacoes.checar_chave_unica(
        final["ruas_regioes"], RUA + ["id_regiao"], "regiões por rua"
    )
    validacoes.checar_chave_unica(
        final["ruas_bairro_regioes"], RUA + ["bairro", "id_regiao"], "regiões por rua e bairro"
    )
    validacoes.checar_chave_unica(
        final["bairros_regioes"], ["cd_municipio_ibge", "bairro", "id_regiao"], "regiões por bairro"
    )

    for nome, df in final.items():
        if "fracao" in df.columns and df["fracao"].isna().any():
            raise validacoes.ValidacaoFalhou(
                f"{nome}: {int(df['fracao'].isna().sum()):,} fração(ões) sem valor — peso zerado "
                "em algum grupo. Publicar isso geraria NaN no JSON, que o navegador não lê."
            )

    print()
    for nome, df in final.items():
        arquivo = "indice_ruas" if nome == "trechos" else ("ruas_dominante" if nome == "dominante" else nome)
        destino = config.DIR_INTERMEDIARIO / f"{arquivo}.parquet"
        df.to_parquet(destino, index=False)
        print(f"  salvo: {destino} ({len(df):,} linhas)")


if __name__ == "__main__":
    main()

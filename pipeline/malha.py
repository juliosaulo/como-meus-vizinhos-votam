"""Quais regiões existem num ano — a definição que a malha de referência usa.

Mora aqui, fora dos passos, porque o passo 22 e o passo 31 precisam da **mesma**
resposta, e porque a definição é mais sutil do que parece.

Região ativa é região com **local de votação funcionando** no ano de referência,
não região com voto apurado. A diferença não aparece quando se roda o pipeline
depois da eleição, que é o caso de sempre: todo local que funcionou tem voto. Ela
aparece no dia em que se quer a malha nova **antes** do resultado — e esse é
exatamente o caso de 2026, porque o TSE publica o eleitorado por seção meses
antes da votação, com os 95.116 locais e suas coordenadas.

Definir pela votação deixaria a malha de 2026 vazia na véspera da eleição: zero
regiões ativas, o passo 22 cairia no fallback de manter as regiões antigas, e o
passo 31 (duas horas de processamento) não atribuiria endereço nenhum às 5.312
regiões que só existem em 2026.

Quando o arquivo de eleitorado não está disponível, a função devolve conjunto
vazio e quem chama volta à definição antiga — assim nada depende do passo 10 ter
rodado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402

ELEITORADO = config.DIR_INTERMEDIARIO / "eleitorado_local.parquet"
DE_PARA = config.DIR_INTERMEDIARIO / "de_para_local_regiao.parquet"


def locais_do_ano(ano: int, caminho: Path | None = None) -> set[str]:
    """Os locais de votação que funcionam no ano, pelo eleitorado por seção."""
    caminho = caminho or ELEITORADO
    if not caminho.exists():
        return set()
    eleitorado = pd.read_parquet(caminho, columns=["id_local_votacao", "ano_eleicao"])
    return set(eleitorado.loc[eleitorado["ano_eleicao"] == ano, "id_local_votacao"])


def regioes_do_ano(ano: int, de_para: pd.DataFrame | None = None,
                   caminho_eleitorado: Path | None = None) -> set[int]:
    """As regiões formadas pelos locais que funcionam no ano.

    Conjunto vazio significa "não sei responder" — o arquivo de eleitorado não
    está lá, ou não tem esse ano. Quem chama decide o que fazer.
    """
    locais = locais_do_ano(ano, caminho_eleitorado)
    if not locais:
        return set()
    if de_para is None:
        if not DE_PARA.exists():
            return set()
        de_para = pd.read_parquet(DE_PARA, columns=["id_local_votacao", "id_regiao"])
    return set(de_para.loc[de_para["id_local_votacao"].isin(locais), "id_regiao"])

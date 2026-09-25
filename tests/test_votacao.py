"""Testes da classificação do tipo de voto (passo 21), com dados fabricados."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

classificar_tipo_voto = __import__("21_processar_votacao").classificar_tipo_voto


def classificar(linhas: list[tuple[str, str]]) -> list[str]:
    df = pd.DataFrame(linhas, columns=["NR_VOTAVEL", "SQ_CANDIDATO"])
    return list(classificar_tipo_voto(df))


def test_codigos_oficiais():
    assert classificar([
        ("95", "-1"), ("96", "-1"), ("97", "-1"), ("17", "-3"), ("1717", "10000601234"),
    ]) == ["branco", "nulo", "anulado", "legenda", "nominal"]


def test_legenda_com_sequencial_vazio_do_df_2018():
    # O caso real: legenda do DF em 2018 veio com sequencial -1 em vez de -3.
    assert classificar([("17", "-1")]) == ["legenda"]


def test_nominal_com_sequencial_vazio_nao_vira_legenda():
    # 4 dígitos é candidato: continua nominal e cai na guarda de partido.
    assert classificar([("1717", "-1")]) == ["nominal"]


def test_presidente_de_dois_digitos_continua_nominal():
    assert classificar([("13", "280001607829")]) == ["nominal"]

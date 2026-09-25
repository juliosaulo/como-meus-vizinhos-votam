"""Testes da consulta endereço → região.

A sequência de decisão (rua → bairro → número → lista) é a operação que o site
executa a cada consulta, e o front-end vai reimplementá-la em JavaScript. Estes
testes fixam o comportamento esperado em casos construídos à mão, onde a
resposta certa é óbvia por inspeção.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import consulta_regiao  # noqa: E402


def rua(trechos, n_regioes=None, provavel=None, regioes=None, bairros=None):
    r = {
        "nome": "RUA DE TESTE",
        "trechos": trechos,
        "n_regioes": n_regioes if n_regioes is not None else len({t[1] for t in trechos}),
        "regiao_provavel": provavel if provavel is not None else (trechos[0][1] if trechos else 0),
        "confianca": 1.0,
    }
    if regioes is not None:
        r["regioes"] = regioes
    if bairros is not None:
        r["bairros"] = bairros
    return r


def resolver(*args, **kwargs):
    r = consulta_regiao.resolver_regiao(*args, **kwargs)
    return r["id_regiao"], r["confianca"]


class TestRuaDeRegiaoUnica:
    def test_dispensa_o_numero(self):
        assert resolver(rua([[1, 87]]), None) == (87, "exata")

    def test_ignora_o_numero_informado(self):
        assert resolver(rua([[1, 87]]), 9999) == (87, "exata")

    def test_ignora_o_bairro_informado(self):
        assert resolver(rua([[1, 87]]), None, "QUALQUER") == (87, "exata")


class TestRuaComVariosTrechos:
    # Rua Dr. Brandão: 1-519 na região 87, 520-1199 na 88, 1200+ de volta na 87.
    TRECHOS = [[1, 87], [520, 88], [1200, 87]]
    REGIOES = [[87, 0.7], [88, 0.3]]

    @pytest.mark.parametrize("numero,esperado", [
        (1, 87), (100, 87), (519, 87),
        (520, 88), (700, 88), (1199, 88),
        (1200, 87), (5000, 87),
    ])
    def test_encontra_o_trecho_certo(self, numero, esperado):
        assert resolver(rua(self.TRECHOS, regioes=self.REGIOES), numero)[0] == esperado

    def test_numero_no_inicio_do_trecho_e_exato(self):
        assert resolver(rua(self.TRECHOS, regioes=self.REGIOES), 520)[1] == "exata"

    def test_numero_entre_dois_inicios_e_interpolado(self):
        assert resolver(rua(self.TRECHOS, regioes=self.REGIOES), 700)[1] == "interpolada"

    def test_numero_abaixo_do_primeiro_trecho_cai_no_primeiro(self):
        # Numeração começando em 100 e usuário digitando 50: melhor responder o
        # primeiro trecho do que não responder.
        assert resolver(rua([[100, 87], [500, 88]], regioes=[[87, .6], [88, .4]]), 50)[0] == 87

    def test_sem_numero_devolve_a_regiao_provavel_e_avisa(self):
        assert resolver(rua(self.TRECHOS, regioes=[[88, 0.6], [87, 0.4]]), None) == (88, "provavel")

    @pytest.mark.parametrize("numero", [0, None])
    def test_zero_e_sem_numero_sao_a_mesma_coisa(self, numero):
        # No CNEFE o zero é a marca de S/N: não pode ser lido como o trecho [0, x].
        r = consulta_regiao.resolver_regiao(
            rua([[0, 91], [500, 92]], regioes=[[92, 0.8], [91, 0.2]]), numero
        )
        assert (r["id_regiao"], r["confianca"]) == (92, "provavel")


class TestBairro:
    TRECHOS = [[1, 10], [500, 20], [900, 30]]
    REGIOES = [[10, 0.5], [20, 0.3], [30, 0.2]]
    BAIRROS = [["CENTRO", [[10, 1.0]]], ["ALTO", [[20, 0.6], [30, 0.4]]]]

    def base(self):
        return rua(self.TRECHOS, regioes=self.REGIOES, bairros=self.BAIRROS)

    def test_bairro_de_regiao_unica_resolve(self):
        assert resolver(self.base(), None, "CENTRO") == (10, "exata")

    def test_bairro_ambiguo_devolve_a_dominante_do_bairro(self):
        id_regiao, confianca = resolver(self.base(), None, "ALTO")
        assert (id_regiao, confianca) == (20, "provavel")

    def test_bairro_restringe_as_opcoes(self):
        r = consulta_regiao.resolver_regiao(self.base(), None, "ALTO")
        assert [o[0] for o in r["opcoes"]] == [20, 30]

    def test_bairro_ignora_acento_e_caixa(self):
        assert resolver(self.base(), None, "centro") == (10, "exata")

    def test_bairro_inexistente_na_rua_e_ignorado(self):
        r = consulta_regiao.resolver_regiao(self.base(), None, "INVENTADO")
        assert r["bairro_ignorado"] is True
        assert r["id_regiao"] == 10  # cai na dominante da rua

    def test_numero_escolhe_dentro_do_bairro(self):
        assert resolver(self.base(), 900, "ALTO") == (30, "exata")

    def test_numero_no_meio_do_trecho_do_bairro_e_interpolado(self):
        assert resolver(self.base(), 950, "ALTO") == (30, "interpolada")

    def test_numero_de_outro_bairro_cai_no_trecho_compativel(self):
        # O número 100 fica no trecho da região 10, que não é do bairro ALTO:
        # a busca caminha até o primeiro trecho que é.
        r = consulta_regiao.resolver_regiao(self.base(), 100, "ALTO")
        assert r["id_regiao"] == 20
        assert r["confianca"] == "interpolada"

    def test_numero_sem_trecho_compativel_e_descartado(self):
        so_centro = rua([[1, 10]], n_regioes=2, regioes=[[10, 0.5], [99, 0.5]],
                        bairros=[["ALTO", [[99, 0.7], [98, 0.3]]]])
        r = consulta_regiao.resolver_regiao(so_centro, 100, "ALTO")
        assert r["conflito_numero_bairro"] is True
        assert r["id_regiao"] == 99  # a dominante do bairro informado


class TestOpcoes:
    def test_sempre_devolve_opcoes_mesmo_quando_unica(self):
        r = consulta_regiao.resolver_regiao(rua([[1, 87]]), None)
        assert r["opcoes"] == [(87, 1.0)]

    def test_opcoes_da_rua_vem_ordenadas(self):
        r = consulta_regiao.resolver_regiao(
            rua([[1, 10], [50, 20]], regioes=[[20, 0.8], [10, 0.2]]), None
        )
        assert [o[0] for o in r["opcoes"]] == [20, 10]


class TestNormalizacaoDoTexto:
    @pytest.mark.parametrize("digitado", [
        "Avenida Brasil", "AVENIDA BRASIL", "avenida brasil", " Avenida Brasil ",
    ])
    def test_ignora_caixa_e_espaco(self, digitado):
        assert consulta_regiao.normalizar(digitado) == "AVENIDA BRASIL"

    def test_ignora_acento(self):
        assert consulta_regiao.normalizar("Rua Dr. Brandão") == consulta_regiao.normalizar("RUA DR. BRANDAO")

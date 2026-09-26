"""Testes das conferências dos arquivos publicados, com dados fabricados."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qualidade import validar_publicado as vp  # noqa: E402


def rua(**campos):
    base = {"nome": "RUA DE TESTE", "regiao_provavel": 1, "n_regioes": 1,
            "confianca": 1.0, "trechos": [[0, 1]]}
    return {**base, **campos}


class TestRua:
    def test_rua_simples_nao_tem_problema(self):
        assert vp.problemas_da_rua(rua(), {1}) == []

    def test_regiao_inexistente_no_municipio(self):
        problemas = vp.problemas_da_rua(rua(trechos=[[0, 1], [50, 99]]), {1})
        assert any("inexistentes" in p for p in problemas)

    def test_rua_ambigua_sem_lista_de_regioes(self):
        problemas = vp.problemas_da_rua(rua(n_regioes=2, trechos=[[0, 1], [9, 2]]), {1, 2})
        assert any("sem a lista" in p for p in problemas)

    def test_fracoes_que_nao_somam_um(self):
        problemas = vp.problemas_da_rua(
            rua(n_regioes=2, trechos=[[0, 1]], regioes=[[1, 0.5], [2, 0.2]]), {1, 2}
        )
        assert any("somam" in p for p in problemas)

    def test_lista_fora_de_ordem(self):
        problemas = vp.problemas_da_rua(
            rua(n_regioes=2, trechos=[[0, 1]], regioes=[[1, 0.3], [2, 0.7]]), {1, 2}
        )
        assert any("ordem" in p for p in problemas)

    def test_lista_longa_tolera_o_arredondamento(self):
        # 200 regiões de peso igual, cada fração arredondada a 3 casas: a soma
        # não fecha 1 exatamente, e isso não é defeito do dado.
        lista = [[i, 0.005] for i in range(1, 201)]
        problemas = vp.problemas_da_rua(
            rua(n_regioes=200, trechos=[[0, 1]], regioes=lista), set(range(1, 201))
        )
        assert problemas == []

    def test_bairro_com_lista_valida(self):
        assert vp.problemas_da_rua(
            rua(n_regioes=2, trechos=[[0, 1], [9, 2]], regioes=[[1, 0.6], [2, 0.4]],
                bairros=[["CENTRO", [[1, 1.0]]]]),
            {1, 2},
        ) == []


def regiao(candidatos, cargo="presidente"):
    return {"resultados": {cargo: {"2022": {"2": candidatos}}}}


class TestRegiao:
    def test_presidente_que_soma_cem(self):
        candidatos = [{"nome": "A", "votos": 70, "pct": 70.0}, {"nome": "B", "votos": 30, "pct": 30.0}]
        assert vp.problemas_da_regiao("1", regiao(candidatos)) == []

    def test_presidente_que_nao_soma_cem(self):
        candidatos = [{"nome": "A", "votos": 70, "pct": 70.0}, {"nome": "B", "votos": 30, "pct": 12.0}]
        assert any("somam" in p for p in vp.problemas_da_regiao("1", regiao(candidatos)))

    def test_deputado_nao_precisa_somar_cem(self):
        # Só os 3 mais votados são publicados.
        candidatos = [{"nome": "A", "votos": 10, "pct": 18.0}, {"nome": "B", "votos": 5, "pct": 7.0}]
        assert vp.problemas_da_regiao("1", regiao(candidatos, "deputado_federal")) == []

    def test_pct_fora_da_faixa(self):
        candidatos = [{"nome": "A", "votos": 1, "pct": 140.0}]
        assert any("fora de 0" in p for p in vp.problemas_da_regiao("1", regiao(candidatos)))

    def test_branco_e_nulo_dentro_da_faixa(self):
        r = {"resultados": {"nao_nominal": {"presidente": {"2022": {"2": {
            "branco": {"votos": 10, "pct": 1.2}, "nulo": {"votos": 20, "pct": 2.4},
        }}}}}}
        assert vp.problemas_da_regiao("1", r) == []


def agregado(candidatos, total=None):
    conteudo = {"presidente": {"2022": {"2": candidatos}}}
    if total is not None:
        conteudo["total_votos"] = {"2022": {"2": total}}
    return conteudo


class TestAgregado:
    BOM = [{"nome": "Fulana", "partido": "AA", "votos": 60, "pct": 60.0},
           {"nome": "Sicrano", "partido": "BB", "votos": 40, "pct": 40.0}]

    def test_agregado_correto(self):
        assert vp.problemas_do_agregado("Brasil", agregado(self.BOM, 125)) == []

    def test_candidato_repetido_na_lista(self):
        # A forma exata do bug: uma linha por local, em vez de uma por candidato.
        repetido = [{"nome": "Fulana", "partido": "AA", "votos": 30, "pct": 50.0},
                    {"nome": "Fulana", "partido": "AA", "votos": 30, "pct": 50.0}]
        assert any("repetido" in p for p in vp.problemas_do_agregado("Brasil", agregado(repetido)))

    def test_lista_com_milhares_de_candidatos(self):
        muitos = [{"nome": f"C{i}", "partido": "AA", "votos": 1, "pct": 0.0} for i in range(3000)]
        problemas = vp.problemas_do_agregado("Brasil", agregado(muitos))
        assert any("não é uma eleição presidencial" in p for p in problemas)

    def test_percentuais_que_nao_fecham(self):
        torto = [{"nome": "Fulana", "partido": "AA", "votos": 60, "pct": 6.0}]
        assert any("somam" in p for p in vp.problemas_do_agregado("Brasil", agregado(torto)))

    def test_validos_maiores_que_o_total(self):
        problemas = vp.problemas_do_agregado("Brasil", agregado(self.BOM, 50))
        assert any("válidos em" in p for p in problemas)


class TestJsonEstrito:
    def test_recusa_nan(self, tmp_path):
        arquivo = tmp_path / "x.json"
        arquivo.write_text('{"fracao": NaN}', encoding="utf-8")
        with pytest.raises(ValueError, match="NaN"):
            vp.ler_json_estrito(arquivo)

    def test_aceita_json_valido(self, tmp_path):
        arquivo = tmp_path / "x.json"
        arquivo.write_text('{"fracao": 0.5}', encoding="utf-8")
        assert vp.ler_json_estrito(arquivo) == {"fracao": 0.5}

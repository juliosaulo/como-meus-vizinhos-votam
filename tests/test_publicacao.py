"""Testes das funções que montam o que vai para o site.

O risco aqui não é o código quebrar — é ele produzir um número plausível e
errado. Uma lista de candidatos com o nome repetido e o voto não somado tem
exatamente a aparência de uma lista correta, e passa despercebida a olho nu.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

publicacao = __import__("40_build_dados_publicados")


def votos(linhas):
    """(local, candidato, partido, tipo, votos) → tabela como a do passo 21."""
    return pd.DataFrame(
        [{"id_local_votacao": l, "sg_uf": "RO", "cd_municipio_ibge": "1100015",
          "ano_eleicao": 2022, "turno": "2", "cargo": "PRESIDENTE",
          "nm_votavel": c, "sg_partido": p, "tipo_voto": t, "qt_votos": v}
         for l, c, p, t, v in linhas]
    )


class TestResultadosPresidente:
    def test_soma_os_votos_do_mesmo_candidato_em_locais_diferentes(self):
        # O caso real: sem somar, a saída vira uma linha por local, com o nome
        # repetido e o total de cada urna no lugar do total do candidato.
        tabela = votos([
            ("RO_1_1_1", "FULANA", "AA", "nominal", 100),
            ("RO_1_1_2", "FULANA", "AA", "nominal", 50),
            ("RO_1_1_1", "SICRANO", "BB", "nominal", 25),
        ])
        saida = publicacao.resultados_presidente(tabela, "cd_municipio_ibge")["1100015"]
        candidatos = saida["presidente"]["2022"]["2"]
        assert [c["nome"] for c in candidatos] == ["Fulana", "Sicrano"]
        assert [c["votos"] for c in candidatos] == [150, 25]

    def test_nenhum_candidato_aparece_duas_vezes(self):
        tabela = votos([(f"RO_1_1_{i}", "FULANA", "AA", "nominal", 10) for i in range(5)])
        candidatos = publicacao.resultados_presidente(tabela, "cd_municipio_ibge")["1100015"]["presidente"]["2022"]["2"]
        assert len(candidatos) == 1
        assert candidatos[0]["votos"] == 50

    def test_percentual_e_sobre_o_voto_valido(self):
        tabela = votos([
            ("RO_1_1_1", "FULANA", "AA", "nominal", 60),
            ("RO_1_1_1", "SICRANO", "BB", "nominal", 40),
            ("RO_1_1_1", "VOTO EM BRANCO", None, "branco", 10),
            ("RO_1_1_1", "VOTO NULO", None, "nulo", 15),
        ])
        saida = publicacao.resultados_presidente(tabela, "cd_municipio_ibge")["1100015"]
        candidatos = saida["presidente"]["2022"]["2"]
        assert sum(c["pct"] for c in candidatos) == 100.0
        assert candidatos[0]["pct"] == 60.0            # 60 de 100 válidos
        assert saida["total_votos"]["2022"]["2"] == 125
        assert saida["nao_nominal"]["2022"]["2"]["branco"]["pct"] == 8.0   # 10 de 125

    def test_ordena_do_mais_votado_ao_menos(self):
        tabela = votos([
            ("RO_1_1_1", "TERCEIRO", "CC", "nominal", 5),
            ("RO_1_1_1", "PRIMEIRO", "AA", "nominal", 90),
            ("RO_1_1_1", "SEGUNDO", "BB", "nominal", 30),
        ])
        candidatos = publicacao.resultados_presidente(tabela, "cd_municipio_ibge")["1100015"]["presidente"]["2022"]["2"]
        assert [c["nome"] for c in candidatos] == ["Primeiro", "Segundo", "Terceiro"]

    def test_separa_por_ano_e_turno(self):
        tabela = votos([("RO_1_1_1", "FULANA", "AA", "nominal", 10)])
        outro = tabela.copy()
        outro["ano_eleicao"] = 2018
        outro["turno"] = "1"
        saida = publicacao.resultados_presidente(pd.concat([tabela, outro]), "cd_municipio_ibge")["1100015"]
        assert sorted(saida["presidente"]) == ["2018", "2022"]
        assert list(saida["presidente"]["2018"]) == ["1"]

    def test_ignora_outros_cargos(self):
        tabela = votos([("RO_1_1_1", "FULANA", "AA", "nominal", 10)])
        tabela.loc[0, "cargo"] = "DEPUTADO FEDERAL"
        assert publicacao.resultados_presidente(tabela, "cd_municipio_ibge") == {}


class TestCompartilhar:
    """O arquivo que o servidor lê para decidir se um link compartilhado existe.

    Um local fora dele devolve 302 para a home: o link que a pessoa mandou no
    grupo não abre. Por isso todo local tem de estar aqui, inclusive o que
    estreou na malha e não tem eleição anterior para mostrar.
    """

    @staticmethod
    def arquivo(tmp_path, resultados):
        dim = pd.DataFrame([
            {"cd_municipio_ibge": "1100049", "id_regiao": 71160, "nm_municipio": "CACOAL",
             "sg_uf": "RO", "nm_local_votacao": "ESCOLA VELHA", "ds_endereco": "LINHA E"},
            {"cd_municipio_ibge": "1100049", "id_regiao": 71169, "nm_municipio": "CACOAL",
             "sg_uf": "RO", "nm_local_votacao": "CACOAL SHOPPING", "ds_endereco": "AV CASTELO"},
        ])
        ids = pd.DataFrame([
            {"id_regiao": 71160, "id_publico": "11-1600", "chaves": ["11-1600"]},
            {"id_regiao": 71169, "id_publico": "11-1708", "chaves": ["11-1708"]},
        ])
        publicacao.config.DIR_PUBLICADO = tmp_path
        publicacao.publicar_compartilhar(dim, resultados, ids)
        return json.loads((tmp_path / "compartilhar" / "1100049.json").read_text("utf-8"))

    def test_local_sem_eleicao_anterior_entra_no_arquivo(self, tmp_path):
        historico = {"presidente": {"2022": {"2": [
            {"nome": "Fulana", "partido": "AA", "pct": 60.0, "votos": 60},
        ]}}}
        locais = self.arquivo(tmp_path, {71160: historico})["locais"]
        assert sorted(locais) == ["11-1600", "11-1708"]
        assert locais["11-1708"]["local"] == "CACOAL SHOPPING"
        # Sem placar: é por isso que a prévia do link dele fica neutra.
        assert "candidatos" not in locais["11-1708"]
        assert "ano" not in locais["11-1708"]
        assert locais["11-1600"]["ano"] == "2022"
        assert locais["11-1600"]["candidatos"][0]["nome"] == "Fulana"

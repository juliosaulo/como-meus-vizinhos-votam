"""Testes da malha de referência: quais regiões existem no ano, e quem usa isso.

A definição é a pergunta central deste módulo, e ela tem duas respostas
possíveis que coincidem quase sempre:

- região **com voto apurado** no ano;
- região **com local de votação funcionando** no ano.

Depois de uma eleição as duas dão o mesmo conjunto. Antes da eleição, a primeira
dá vazio — e é por isso que ela não serve para montar a malha de 2026 na véspera.
Estes testes fixam o comportamento nas duas situações.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import config  # noqa: E402
import malha  # noqa: E402

p22 = __import__("22_votos_por_regiao")
p40 = __import__("40_build_dados_publicados")


def eleitorado(tmp_path: Path, linhas: list[tuple[str, int]]) -> Path:
    """(id_local_votacao, ano) → o arquivo que o passo 10 grava."""
    caminho = tmp_path / "eleitorado_local.parquet"
    pd.DataFrame([{"id_local_votacao": local, "ano_eleicao": ano, "turno": "1",
                   "qt_eleitores": 100} for local, ano in linhas]).to_parquet(caminho)
    return caminho


DE_PARA = pd.DataFrame([
    {"id_local_votacao": "RO_00310_17_1074", "id_regiao": 1},
    {"id_local_votacao": "RO_00310_17_1082", "id_regiao": 1},   # mesmo prédio
    {"id_local_votacao": "RO_00310_17_1090", "id_regiao": 2},
    {"id_local_votacao": "RO_00310_17_1104", "id_regiao": 3},
])


class TestRegioesDoAno:
    def test_locais_do_ano_viram_regioes(self, tmp_path):
        caminho = eleitorado(tmp_path, [("RO_00310_17_1074", 2026),
                                        ("RO_00310_17_1090", 2026)])
        assert malha.regioes_do_ano(2026, DE_PARA, caminho) == {1, 2}

    def test_dois_locais_no_mesmo_predio_dao_uma_regiao(self, tmp_path):
        caminho = eleitorado(tmp_path, [("RO_00310_17_1074", 2026),
                                        ("RO_00310_17_1082", 2026)])
        assert malha.regioes_do_ano(2026, DE_PARA, caminho) == {1}

    def test_ano_sem_locais_devolve_vazio(self, tmp_path):
        caminho = eleitorado(tmp_path, [("RO_00310_17_1074", 2022)])
        assert malha.regioes_do_ano(2026, DE_PARA, caminho) == set()

    def test_sem_o_arquivo_devolve_vazio(self, tmp_path):
        """"Não sei responder" é diferente de "nenhuma região"."""
        assert malha.regioes_do_ano(2026, DE_PARA, tmp_path / "nao_existe.parquet") == set()

    def test_local_fora_do_de_para_e_ignorado(self, tmp_path):
        """Local sem coordenada não forma região — não dá para exibi-lo."""
        caminho = eleitorado(tmp_path, [("RO_00310_17_9999", 2026)])
        assert malha.regioes_do_ano(2026, DE_PARA, caminho) == set()


def votos_por_regiao(linhas: list[tuple[int, int]]) -> pd.DataFrame:
    """(id_regiao, ano) → o que o passo 22 recebe depois da junção.

    Mesmo vazia a tabela tem as colunas, como teria vinda de um parquet.
    """
    return pd.DataFrame(
        [{"id_regiao": regiao, "ano_eleicao": ano, "qt_votos": 10} for regiao, ano in linhas],
        columns=["id_regiao", "ano_eleicao", "qt_votos"],
    )


class TestRegioesAtivasNaReferencia:
    """O passo 22 une as duas definições: nenhuma região sai da malha por conta
    da mudança, e as que ainda não votaram entram."""

    def test_depois_da_eleicao_as_duas_definicoes_coincidem(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2026)
        monkeypatch.setattr(malha, "ELEITORADO",
                            eleitorado(tmp_path, [("RO_00310_17_1074", 2026),
                                                  ("RO_00310_17_1090", 2026)]))
        ativas = p22.regioes_ativas_na_referencia(votos_por_regiao([(1, 2026), (2, 2026)]),
                                                  DE_PARA)
        assert ativas == {1, 2}

    def test_antes_da_eleicao_a_malha_vem_dos_locais(self, tmp_path, monkeypatch):
        """O caso de sábado: 2026 é a referência e ainda não há um voto."""
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2026)
        monkeypatch.setattr(malha, "ELEITORADO",
                            eleitorado(tmp_path, [("RO_00310_17_1074", 2026),
                                                  ("RO_00310_17_1104", 2026)]))
        ativas = p22.regioes_ativas_na_referencia(votos_por_regiao([(1, 2022), (2, 2022)]),
                                                  DE_PARA)
        assert ativas == {1, 3}

    def test_regiao_com_voto_mas_sem_eleitorado_nao_e_perdida(self, tmp_path, monkeypatch):
        """União, não substituição: trocar a definição não pode tirar do ar uma
        região que já estava publicada."""
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2022)
        monkeypatch.setattr(malha, "ELEITORADO",
                            eleitorado(tmp_path, [("RO_00310_17_1074", 2022)]))
        ativas = p22.regioes_ativas_na_referencia(votos_por_regiao([(1, 2022), (2, 2022)]),
                                                  DE_PARA)
        assert ativas == {1, 2}

    def test_sem_eleitorado_cai_na_definicao_pelo_voto(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2022)
        monkeypatch.setattr(malha, "ELEITORADO", tmp_path / "nao_existe.parquet")
        ativas = p22.regioes_ativas_na_referencia(votos_por_regiao([(2, 2022)]), DE_PARA)
        assert ativas == {2}


class TestRegioesAPublicar:
    """O passo 40 precisa publicar a região mesmo sem resultado, senão o índice
    de ruas aponta para uma região que não está no JSON."""

    def dim(self):
        return pd.DataFrame([{"id_regiao": r, "cd_municipio_ibge": "1100015"}
                             for r in (1, 2, 3)])

    def test_regiao_sem_resultado_entra_se_esta_na_malha(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2026)
        monkeypatch.setattr(malha, "ELEITORADO",
                            eleitorado(tmp_path, [("RO_00310_17_1074", 2026),
                                                  ("RO_00310_17_1104", 2026)]))
        monkeypatch.setattr(malha, "DE_PARA", tmp_path / "de_para.parquet")
        DE_PARA.to_parquet(malha.DE_PARA)
        saida = p40.regioes_a_publicar(self.dim(), votos_por_regiao([(1, 2022)]))
        assert set(saida["id_regiao"]) == {1, 3}      # 3 não tem voto e entra

    def test_regiao_fora_da_malha_mas_com_voto_continua_entrando(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2026)
        monkeypatch.setattr(malha, "ELEITORADO", tmp_path / "nao_existe.parquet")
        saida = p40.regioes_a_publicar(self.dim(), votos_por_regiao([(2, 2022)]))
        assert set(saida["id_regiao"]) == {2}

    def test_nao_publica_regiao_que_nao_existe_em_nenhum_dos_dois(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ANO_REFERENCIA_MALHA", 2026)
        monkeypatch.setattr(malha, "ELEITORADO",
                            eleitorado(tmp_path, [("RO_00310_17_1074", 2026)]))
        monkeypatch.setattr(malha, "DE_PARA", tmp_path / "de_para.parquet")
        DE_PARA.to_parquet(malha.DE_PARA)
        saida = p40.regioes_a_publicar(self.dim(), votos_por_regiao([]))
        assert set(saida["id_regiao"]) == {1}

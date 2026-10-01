"""Testes da leitura da base oficial de locais (passo 10) e do complemento da
malha (passo 11), com dados fabricados."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from qualidade.validacoes import ValidacaoFalhou  # noqa: E402

p10 = __import__("10_locais_oficiais")
p11 = __import__("11_montar_dim_regiao")


def secoes(linhas):
    """Monta o que o CSV do TSE entrega: uma linha por seção, tudo texto."""
    colunas = ["AA_ELEICAO", "NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA",
               "NR_SECAO", "NR_LOCAL_VOTACAO", "NM_LOCAL_VOTACAO", "DS_ENDERECO",
               "NR_LATITUDE", "NR_LONGITUDE", "QT_ELEITOR_ELEICAO_FEDERAL"]
    return pd.DataFrame(linhas, columns=colunas).astype(str)


UMA_SECAO = ("2022", "1", "RO", "310", "ALTA FLORESTA", "17", "1", "1074",
             "ESCOLA X", "RUA Y, 10", "-12,129643", "-61,993019", "250")


class TestLeitura:
    def test_identificador_e_coordenada(self):
        df = p10.preparar(secoes([UMA_SECAO]))
        assert df.loc[0, "id_local_votacao"] == "RO_00310_17_1074"
        assert df.loc[0, "latitude"] == pytest.approx(-12.129643)
        assert df.loc[0, "qt_eleitores"] == 250

    def test_ponto_e_virgula_decimal(self):
        com_ponto = list(UMA_SECAO[:10]) + ["-12.129643", "-61.993019", "250"]
        assert p10.preparar(secoes([tuple(com_ponto)])).loc[0, "latitude"] == pytest.approx(-12.129643)

    @pytest.mark.parametrize("lat,lon", [("-1", "-1"), ("0", "0"), ("48,27", "90,40")])
    def test_coordenada_invalida_vira_ausente(self, lat, lon):
        # -1 é o "não informado" do TSE; a caixa do Brasil pega o resto.
        linha = list(UMA_SECAO[:10]) + [lat, lon, "250"]
        df = p10.preparar(secoes([tuple(linha)]))
        assert df.loc[0, "latitude"] is pd.NA or pd.isna(df.loc[0, "latitude"])

    def test_eleitorado_soma_as_secoes_do_local(self):
        outra = list(UMA_SECAO); outra[6] = "2"; outra[12] = "180"
        df = p10.preparar(secoes([UMA_SECAO, tuple(outra)]))
        eleitorado = p10.montar_eleitorado(df)
        assert len(eleitorado) == 1
        assert eleitorado.loc[0, "qt_eleitores"] == 430

    def test_local_fica_com_a_eleicao_mais_recente(self):
        antiga = list(UMA_SECAO); antiga[0] = "2018"; antiga[8] = "NOME ANTIGO"
        df = p10.preparar(secoes([tuple(antiga), UMA_SECAO]))
        locais = p10.montar_locais(df)
        assert len(locais) == 1
        assert locais.loc[0, "nm_local_votacao"] == "ESCOLA X"
        assert locais.loc[0, "ano_referencia"] == 2022

    def test_local_sem_coordenada_nenhuma_fica_de_fora(self):
        sem = list(UMA_SECAO); sem[10] = "-1"; sem[11] = "-1"
        assert p10.montar_locais(p10.preparar(secoes([tuple(sem)]))).empty

    def test_brasil_e_ignorado_quando_ha_arquivo_por_uf(self):
        class ZipFalso:
            def __init__(self, nomes): self._nomes = nomes
            def namelist(self): return self._nomes

        assert p10.csvs_do_zip(ZipFalso(["x_SP.csv", "x_BRASIL.csv"])) == ["x_SP.csv"]
        assert p10.csvs_do_zip(ZipFalso(["x.csv"])) == ["x.csv"]


def artefato(linhas):
    colunas = ["id_local_votacao", "sg_uf", "nm_municipio", "cd_municipio_ibge",
               "nm_local_votacao_consolidado", "ds_local_votacao_endereco_consolidado",
               "status_geocodificacao", "latitude_final", "longitude_final", "origem_coordenada"]
    return pd.DataFrame(linhas, columns=colunas)


class TestComplemento:
    def test_coordenada_ambigua_entre_municipios_e_descartada(self):
        locais = artefato([
            ["RO_00310_17_1", "RO", "A", "1100015", "X", "R", "top1_auto", -12.1, -61.9, "cnefe"],
            ["RO_00320_18_1", "RO", "B", "1100023", "Y", "S", "oficial_tse", -12.1, -61.9, "tse"],
        ])
        saida = p11.descartar_coordenada_ambigua(locais)
        assert saida.loc[0, "latitude_final"] == -12.1       # a nossa fica
        assert pd.isna(saida.loc[1, "latitude_final"])       # a do TSE sai

    def test_ponto_sem_conflito_fica_intacto(self):
        locais = artefato([
            ["RO_00310_17_1", "RO", "A", "1100015", "X", "R", "top1_auto", -12.1, -61.9, "cnefe"],
            ["RO_00310_17_2", "RO", "A", "1100015", "Y", "S", "oficial_tse", -12.1, -61.9, "tse"],
        ])
        saida = p11.descartar_coordenada_ambigua(locais)
        assert saida["latitude_final"].notna().all()

    def test_local_novo_encosta_no_predio_vizinho(self):
        base = artefato([["RO_00310_17_1", "RO", "A", "1100015", "X", "R", "top1_auto",
                          -12.129643, -61.993019, "cnefe"]])
        # ~20 m ao lado: é a mesma escola, com outra zona.
        novos = artefato([["RO_00310_18_1", "RO", "A", "1100015", "X2", "R2", "oficial_tse",
                           -12.129823, -61.993019, "tse"]])
        saida = p11.encostar_em_regiao_existente(novos, base)
        assert saida.loc[0, "latitude_final"] == pytest.approx(-12.129643)
        assert saida.loc[0, "origem_coordenada"] == "cnefe"

    def test_local_novo_distante_mantem_a_propria_coordenada(self):
        base = artefato([["RO_00310_17_1", "RO", "A", "1100015", "X", "R", "top1_auto",
                          -12.129643, -61.993019, "cnefe"]])
        novos = artefato([["RO_00310_18_1", "RO", "A", "1100015", "X2", "R2", "oficial_tse",
                           -12.200000, -61.993019, "tse"]])
        saida = p11.encostar_em_regiao_existente(novos, base)
        assert saida.loc[0, "latitude_final"] == pytest.approx(-12.2)
        assert saida.loc[0, "origem_coordenada"] == "tse"

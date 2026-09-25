"""Testes das guardas de qualidade.

Cada teste corresponde a um erro que já aconteceu na construção desta base (a
história de cada um está na docstring da função testada, em
`qualidade/validacoes.py`). Rodam em milissegundos, com dados fabricados — não
dependem dos ~8 GB de arquivos brutos.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qualidade import validacoes  # noqa: E402
from qualidade.validacoes import ValidacaoFalhou  # noqa: E402


class TestCodigoMunicipio:
    def test_completa_com_zero_a_esquerda(self):
        entrada = pd.Series(["1234", "12345", " 567 "])
        assert list(validacoes.normalizar_codigo_municipio(entrada)) == ["01234", "12345", "00567"]

    def test_o_mesmo_municipio_com_e_sem_zero_vira_a_mesma_chave(self):
        # O bug real: 2018 exportava sem zero à esquerda, os outros anos com —
        # e o mesmo prédio virava dois locais de votação diferentes.
        de_2018 = validacoes.normalizar_codigo_municipio(pd.Series(["1234"]))
        de_2022 = validacoes.normalizar_codigo_municipio(pd.Series(["01234"]))
        assert de_2018.iloc[0] == de_2022.iloc[0]

    def test_recusa_largura_irregular(self):
        with pytest.raises(ValidacaoFalhou, match="5 dígitos"):
            validacoes.checar_codigo_municipio(pd.Series(["1234", "12345"]), "teste")


class TestTurno:
    def test_vira_string(self):
        assert list(validacoes.normalizar_turno(pd.Series([1, 2]))) == ["1", "2"]

    def test_recusa_somar_dois_turnos(self):
        df = pd.DataFrame({"turno": ["1", "2"], "qt_votos": [10, 20]})
        with pytest.raises(ValidacaoFalhou, match="mais de um turno"):
            validacoes.checar_turno_unico(df, "presidente 2022")

    def test_aceita_turno_unico(self):
        df = pd.DataFrame({"turno": ["2", "2"], "qt_votos": [10, 20]})
        validacoes.checar_turno_unico(df, "presidente 2022")


class TestEleicaoSuplementar:
    def test_descarta_nao_ordinaria(self):
        # "1" é eleição suplementar empilhada sob o mesmo ano — se somada,
        # infla o total (aconteceu com MT/Senador 2018 e RR/Governador 2022).
        df = pd.DataFrame({"CD_TIPO_ELEICAO": ["2", "2", "1"], "qt_votos": [10, 20, 999]})
        resultado = validacoes.filtrar_eleicao_ordinaria(df)
        assert len(resultado) == 2
        assert resultado["qt_votos"].sum() == 30

    def test_falha_sem_a_coluna(self):
        with pytest.raises(ValidacaoFalhou, match="ausente"):
            validacoes.filtrar_eleicao_ordinaria(pd.DataFrame({"qt_votos": [1]}))


class TestVotoBranco:
    def test_unifica_as_duas_grafias(self):
        entrada = pd.Series(["VOTO BRANCO", "VOTO EM BRANCO", "VOTO NULO"])
        resultado = validacoes.unificar_voto_branco(entrada)
        assert resultado.iloc[0] == resultado.iloc[1] == "VOTO EM BRANCO"
        assert resultado.iloc[2] == "VOTO NULO"

    def test_agrupamento_soma_as_duas_grafias_juntas(self):
        df = pd.DataFrame({
            "nome": ["VOTO BRANCO", "VOTO EM BRANCO"],
            "votos": [10, 5],
        })
        df["nome"] = validacoes.unificar_voto_branco(df["nome"])
        assert df.groupby("nome")["votos"].sum().loc["VOTO EM BRANCO"] == 15


class TestChaveUnica:
    def test_aceita_chave_composta_correta(self):
        # id_cnefe sozinho se repete; com cod_especie, não.
        df = pd.DataFrame({"id_cnefe": ["1", "1"], "cod_especie": ["1", "6"]})
        validacoes.checar_chave_unica(df, ["id_cnefe", "cod_especie"], "cnefe")

    def test_recusa_chave_incompleta(self):
        df = pd.DataFrame({"id_cnefe": ["1", "1"], "cod_especie": ["1", "6"]})
        with pytest.raises(ValidacaoFalhou, match="não formam chave única"):
            validacoes.checar_chave_unica(df, ["id_cnefe"], "cnefe")


class TestSomaEDeduplicacao:
    def test_aceita_soma_preservada(self):
        validacoes.checar_soma_preservada(pd.Series([1, 2, 3]), pd.Series([3, 3]), "agregação")

    def test_recusa_voto_perdido(self):
        with pytest.raises(ValidacaoFalhou, match="soma mudou"):
            validacoes.checar_soma_preservada(pd.Series([1, 2, 3]), pd.Series([3, 2]), "agregação")

    def test_recusa_municipio_ambiguo_na_mesma_coordenada(self):
        df = pd.DataFrame({
            "lat": [1.0, 1.0], "lon": [2.0, 2.0],
            "cd_municipio_ibge": ["1100015", "1100023"],
        })
        with pytest.raises(ValidacaoFalhou, match="ambíguo"):
            validacoes.checar_constante_por_grupo(
                df, ["lat", "lon"], ["cd_municipio_ibge"], "coordenada"
            )


class TestGeografia:
    def test_recusa_calculo_em_graus(self):
        with pytest.raises(ValidacaoFalhou, match="metros"):
            validacoes.checar_crs_metrico("EPSG:4674", "vizinho mais próximo")

    def test_aceita_projecao_metrica(self):
        validacoes.checar_crs_metrico("EPSG:5880", "vizinho mais próximo")

    def test_detecta_latitude_trocada_com_longitude(self):
        # Coordenada de Porto Velho com os eixos invertidos: continua "válida",
        # mas aponta para fora do Brasil.
        with pytest.raises(ValidacaoFalhou, match="fora do território"):
            validacoes.checar_coordenadas_no_brasil(
                pd.Series([-63.9]), pd.Series([-8.76]), "cnefe"
            )

    def test_aceita_coordenada_brasileira(self):
        validacoes.checar_coordenadas_no_brasil(
            pd.Series([-8.76]), pd.Series([-63.9]), "cnefe"
        )


class TestTotaisOficiais:
    def test_aceita_total_exato(self):
        validacoes.validar_totais_oficiais(
            {"LUIZ INACIO LULA DA SILVA": 60_345_999, "JAIR MESSIAS BOLSONARO": 58_206_354}, 2022, "2"
        )

    def test_recusa_um_voto_de_diferenca(self):
        with pytest.raises(ValidacaoFalhou, match="não bate"):
            validacoes.validar_totais_oficiais(
                {"LUIZ INACIO LULA DA SILVA": 60_346_000, "JAIR MESSIAS BOLSONARO": 58_206_354}, 2022, "2"
            )

    def test_confere_primeiro_turno(self):
        # 2026 só terá 1º turno de início: a checagem não pode depender do 2º.
        with pytest.raises(ValidacaoFalhou, match="1º turno"):
            validacoes.validar_totais_oficiais(
                {"LUIZ INACIO LULA DA SILVA": 60_345_999, "JAIR MESSIAS BOLSONARO": 58_206_354}, 2022, "1"
            )

    def test_turno_sem_referencia_nao_quebra(self):
        validacoes.validar_totais_oficiais({"FULANO": 10}, 2014, "1")


class TestTaxaDeJuncao:
    def test_recusa_juncao_vazia(self):
        # O caso real: identificador montado sem os separadores que a base de
        # referência usa. O merge devolveu zero linhas, sem erro nenhum.
        with pytest.raises(ValidacaoFalhou, match="contrapartida"):
            validacoes.checar_taxa_de_juncao(0, 1000, "votos × regiões")

    def test_aceita_juncao_esperada(self):
        validacoes.checar_taxa_de_juncao(884, 1000, "votos × regiões", minimo=0.5)

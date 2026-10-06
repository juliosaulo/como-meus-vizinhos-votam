"""Testes dos estáticos da camada ao vivo (`coleta_2026/ao_vivo.py`).

Estes três arquivos são a ponte entre o que o TSE publica e a pergunta do site.
O TSE indexa o arquivo de urna por município/zona/seção; a nossa pergunta é por
região — um grupo de locais na mesma coordenada. Só nós sabemos fazer essa
ligação, e se ela sair errada o navegador pede a urna errada, ou nenhuma.

O que não dá para testar aqui é a leitura dos boletins no navegador: isso está em
`coleta_2026/teste_bu_js.py` e `coleta_2026/teste_ao_vivo_js.py`, que rodam o
JavaScript de verdade contra boletins reais.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "coleta_2026"))
sys.path.insert(0, str(RAIZ / "pipeline"))

import ao_vivo  # noqa: E402
import converter  # noqa: E402
import tse  # noqa: E402

COLUNAS = ["AA_ELEICAO", "NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA",
           "NR_SECAO", "NR_LOCAL_VOTACAO", "NM_LOCAL_VOTACAO", "DS_ENDERECO",
           "NR_LATITUDE", "NR_LONGITUDE", "QT_ELEITOR_ELEICAO_FEDERAL"]


def cadastro(linhas: list[tuple[int, int, int]], uf: str = "RR",
             municipio: str = "03018") -> pd.DataFrame:
    """(zona, seção, local) → o CSV do TSE, uma linha por seção."""
    return pd.DataFrame(
        [(2026, 1, uf, municipio, "BOA VISTA", zona, secao, local,
          "ESCOLA X", "RUA Y, 10", "-2,81", "-60,67", "250")
         for zona, secao, local in linhas],
        columns=COLUNAS).astype(str)


DE_PARA = pd.DataFrame([
    {"id_local_votacao": "RR_03018_1_1015", "id_regiao": 71},
    {"id_local_votacao": "RR_03018_1_1023", "id_regiao": 71},   # mesmo prédio
    {"id_local_votacao": "RR_03018_1_1031", "id_regiao": 72},
])
DIM = pd.DataFrame([{"id_regiao": 71, "cd_municipio_ibge": "1400100"},
                    {"id_regiao": 72, "cd_municipio_ibge": "1400100"}])


class TestMontarMapa:
    def test_regiao_junta_as_secoes_dos_dois_locais(self):
        bruto = cadastro([(1, 1, 1015), (1, 2, 1015), (1, 3, 1023)])
        mapa = ao_vivo.montar_mapa(bruto, DE_PARA, DIM, None)
        assert list(mapa) == ["1400100"]
        assert mapa["1400100"]["regioes"]["71"] == [[1, 1015, 1], [1, 1015, 2], [1, 1023, 3]]

    def test_traz_o_codigo_do_tse_e_a_uf(self):
        """O site tem o código do IBGE; o TSE indexa pelo dele. A ponte vai aqui."""
        mapa = ao_vivo.montar_mapa(cadastro([(1, 1, 1015)]), DE_PARA, DIM, None)
        assert mapa["1400100"]["municipio_tse"] == "03018"
        assert mapa["1400100"]["uf"] == "rr"

    def test_secao_agregada_fica_de_fora(self):
        """Agregada não tem urna própria: pedir a dela daria 404, e 404 pode
        bloquear o IP de quem está visitando o site."""
        bruto = cadastro([(1, 1, 1015), (1, 2, 1015)])
        principais = {("03018", "0001", 1)}
        mapa = ao_vivo.montar_mapa(bruto, DE_PARA, DIM, principais)
        assert mapa["1400100"]["regioes"]["71"] == [[1, 1015, 1]]

    def test_local_sem_regiao_nao_entra(self):
        bruto = cadastro([(1, 1, 9999)])
        assert ao_vivo.montar_mapa(bruto, DE_PARA, DIM, None) == {}

    def test_duas_regioes_no_mesmo_municipio(self):
        bruto = cadastro([(1, 1, 1015), (1, 2, 1031)])
        mapa = ao_vivo.montar_mapa(bruto, DE_PARA, DIM, None)
        assert sorted(mapa["1400100"]["regioes"]) == ["71", "72"]

    def test_secoes_saem_ordenadas(self):
        """Ordem estável deixa o arquivo comparável entre gerações."""
        bruto = cadastro([(1, 9, 1015), (1, 2, 1015), (1, 5, 1015)])
        mapa = ao_vivo.montar_mapa(bruto, DE_PARA, DIM, None)
        assert mapa["1400100"]["regioes"]["71"] == [[1, 1015, 2], [1, 1015, 5], [1, 1015, 9]]


def candidatos_fabricados() -> pd.DataFrame:
    linhas = [
        ("PRESIDENTE", "BR", "13", "LULA", "13", "PT"),
        ("PRESIDENTE", "BR", "22", "FLAVIO BOLSONARO", "22", "PL"),
        ("DEPUTADO FEDERAL", "RR", "1234", "FULANO DE TAL", "12", "PDT"),
        ("DEPUTADO FEDERAL", "SP", "1234", "OUTRO FULANO", "12", "PDT"),
        ("SENADOR", "RR", "123", "NAO DEVE APARECER", "12", "PDT"),
    ]
    return pd.DataFrame(
        [{"cargo": cargo, "SG_UE": ue, "NR_CANDIDATO": numero,
          "NM_URNA_CANDIDATO": nome, "NR_PARTIDO": partido, "SG_PARTIDO": sigla}
         for cargo, ue, numero, nome, partido, sigla in linhas])


class TestMontarCandidatos:
    @pytest.fixture(autouse=True)
    def cadastro_fixo(self, monkeypatch):
        monkeypatch.setattr(converter, "cadastro_de_candidatos",
                            lambda ano, pasta=None: candidatos_fabricados())

    def test_presidente_fica_numa_tabela_nacional(self):
        saida = ao_vivo.montar_candidatos(2026)
        assert saida["presidente"]["13"] == {"nome": "Lula", "partido": "PT"}

    def test_deputado_federal_fica_por_uf(self):
        """O mesmo número é outro candidato em cada estado — por isso a tabela
        é por unidade eleitoral, nunca nacional."""
        saida = ao_vivo.montar_candidatos(2026)
        assert saida["RR"]["1234"]["nome"] == "Fulano De Tal"
        assert saida["SP"]["1234"]["nome"] == "Outro Fulano"

    def test_cargo_fora_da_lista_nao_entra(self):
        saida = ao_vivo.montar_candidatos(2026)
        assert all("Nao Deve Aparecer" != c["nome"]
                   for tabela in saida.values() for c in tabela.values())

    def test_cargos_do_ensaio_municipal(self):
        saida = ao_vivo.montar_candidatos(2026, cargos=[11, 13])
        assert saida["presidente"] == {}


class TestNomeDoCargo:
    def test_usa_o_mesmo_nome_do_passo_40(self):
        assert ao_vivo.nome_do_cargo(1) == "presidente"
        assert ao_vivo.nome_do_cargo(6) == "deputado_federal"
        assert ao_vivo.nome_do_cargo(11) == "prefeito"


class TestCSPDoServidor:
    """A camada ao vivo busca no TSE, de dentro do navegador.

    O site serve um Content-Security-Policy restrito, e `connect-src` é o que
    decide se essa busca sai. Com `'self'` sozinho, como estava, o navegador
    recusa toda chamada ao TSE e a apuração ao vivo simplesmente não acontece —
    sem erro visível na tela, só uma linha no console. Não há como pegar isso
    testando a página num servidor local sem o cabeçalho; então testa-se o
    cabeçalho.
    """

    @staticmethod
    def conectar_permitidos() -> list[str]:
        linha = next(l for l in (RAIZ / "site/.htaccess").read_text("utf-8").splitlines()
                     if "Content-Security-Policy" in l)
        csp = re.search(r'"(.*)"', linha).group(1)
        diretiva = next(d for d in csp.split(";") if d.strip().startswith("connect-src"))
        return diretiva.split()[1:]

    def test_permite_o_servidor_de_resultados_do_tse(self):
        assert tse.BASE in self.conectar_permitidos()

    def test_nao_abre_para_qualquer_origem(self):
        assert "*" not in self.conectar_permitidos()


class TestEleicaoDeCadaCargo:
    """O endereço do resultado agregado leva o código da eleição.

    Presidente é apurado na eleição federal; governador, senador e os deputados,
    na estadual, porque a unidade eleitoral deles é a UF. Usar o código da
    federal para todos pedia o deputado no lugar errado e recebia 404 — e a
    comparação com município, estado e Brasil ficava vazia na tela, sem erro
    nenhum aparecendo.
    """

    class ClienteFalso:
        def __init__(self, eleicoes):
            self._eleicoes = eleicoes

        def eleicoes(self):
            return self._eleicoes

    @staticmethod
    def eleicao(codigo, nome, turno="1", ciclo="ele2026"):
        return tse.Eleicao(codigo=codigo, nome=nome, turno=turno, ciclo=ciclo,
                           pleito="3220", data="04/10/2026")

    def cliente(self):
        return self.ClienteFalso([
            self.eleicao("6257", "Eleição Ordinária Federal - 2026 1º Turno"),
            self.eleicao("6259", "Eleição Ordinária Estadual - 2026 1º Turno"),
            self.eleicao("6261", "Eleição Ordinária Municipal - 2026 1º Turno"),
            self.eleicao("9999", "Eleição Ordinária Estadual - 2022 1º Turno", ciclo="ele2022"),
        ])

    def test_presidente_na_federal_e_deputado_na_estadual(self):
        federal = self.eleicao("6257", "Eleição Ordinária Federal - 2026 1º Turno")
        saida = tse.eleicoes_por_cargo(self.cliente(), 2026, 1, [1, 6], federal)
        assert saida == {"1": "6257", "6": "6259"}

    def test_cargos_municipais_saem_na_municipal(self):
        federal = self.eleicao("6257", "Eleição Ordinária Federal - 2026 1º Turno")
        saida = tse.eleicoes_por_cargo(self.cliente(), 2026, 1, [11, 13], federal)
        assert saida == {"11": "6261", "13": "6261"}

    def test_nao_mistura_ciclos(self):
        """A estadual de 2022 está na mesma lista e não pode ser escolhida."""
        federal = self.eleicao("6257", "Eleição Ordinária Federal - 2026 1º Turno")
        saida = tse.eleicoes_por_cargo(self.cliente(), 2026, 1, [6], federal)
        assert saida["6"] == "6259"

    def test_cargo_sem_eleicao_fica_de_fora(self):
        """Melhor o navegador cair no padrão do que apontar para a errada."""
        cliente = self.ClienteFalso([
            self.eleicao("6257", "Eleição Ordinária Federal - 2026 1º Turno")])
        federal = self.eleicao("6257", "Eleição Ordinária Federal - 2026 1º Turno")
        saida = tse.eleicoes_por_cargo(cliente, 2026, 1, [1, 6], federal)
        assert saida == {"1": "6257"}

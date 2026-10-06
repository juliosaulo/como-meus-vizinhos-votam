"""Testes da coleta de 2026 — leitor de DER, parser do BU, conversão e conferência.

Dois tipos de teste convivem aqui, de propósito:

- **Com bytes fabricados.** A função `boletim()` monta um BU inteiro em DER, campo
  por campo. Serve de documentação executável do formato: quem quiser entender a
  estrutura que `bu.py` percorre por posição lê este construtor.
- **Com um BU real.** `tests/fixtures/bu_exemplo_2024.dat` é o boletim publicado da
  seção 1, zona 17, local 1074 de Alta Floresta/RO em 2024 — o mesmo local que
  já aparece nos testes do passo 10. O teste fabricado prova que a regra está
  certa; o arquivo real prova que a regra serve para o que o TSE publica.
"""

from __future__ import annotations

import http.client
import types
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "coleta_2026"))

import bu  # noqa: E402
import coletar  # noqa: E402
import conferir  # noqa: E402
import domingo  # noqa: E402
import converter  # noqa: E402
import der  # noqa: E402
import tse  # noqa: E402

BU_REAL = Path(__file__).resolve().parent / "fixtures" / "bu_exemplo_2024.dat"


# ----------------------------------------------------------------- fábrica de DER

def _tamanho(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    corpo = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(corpo)]) + corpo


def _no(etiqueta: int, conteudo: bytes) -> bytes:
    return bytes([etiqueta]) + _tamanho(len(conteudo)) + conteudo


def seq(*filhos: bytes) -> bytes:
    return _no(0x30, b"".join(filhos))


def inteiro(valor: int) -> bytes:
    tamanho = max(1, (valor.bit_length() + 8) // 8)
    return _no(0x02, valor.to_bytes(tamanho, "big", signed=True))


def bytes_puros(conteudo: bytes) -> bytes:
    return _no(0x04, conteudo)


def enumerado(valor: int) -> bytes:
    return _no(0x0A, bytes([valor]))


def ctx(numero: int, valor: int) -> bytes:
    """Campo com etiqueta de contexto guardando um inteiro.

    Com etiqueta implícita o conteúdo segue a codificação de INTEGER, que é com
    sinal: 200 vira `00 C8`, e não `C8` — sem o zero à esquerda, um leitor que
    respeite o DER entenderia −56. O arquivo real do TSE traz o zero.
    """
    tamanho = max(1, (valor.bit_length() + 8) // 8)
    return _no(0x80 | numero, valor.to_bytes(tamanho, "big", signed=True))


def ctx_construido(numero: int, *filhos: bytes) -> bytes:
    return _no(0xA0 | numero, b"".join(filhos))


def votavel(tipo: int, quantidade: int, partido: int | None = None,
            numero: int | None = None) -> bytes:
    """Um votável como o BU o grava.

    `TotalVotosVotavel` traz tipo e quantidade em etiquetas de contexto ([1] e
    [2]) — e é por essas duas que o parser reconhece um votável quando o formato
    muda de posição entre eleições.

    Voto em alguém — nominal ou legenda — traz `ctx3` com partido e número (na
    legenda os dois são o número do partido). Branco e nulo não trazem
    identificação nenhuma: o terceiro campo já é o ordinal do votável.
    """
    campos = [ctx(1, tipo), ctx(2, quantidade)]
    if partido is not None:
        campos.append(ctx_construido(3, inteiro(partido),
                                     inteiro(partido if numero is None else numero)))
    campos += [inteiro(1), bytes_puros(b"hash de 28 bytes exatamente.")]
    return seq(*campos)


def boletim(municipio: int = 310, zona: int = 17, local: int = 1074, secao: int = 1,
            pleito: int = 452, eleicao: int = 619, cargo: int = 11,
            comparecimento: int = 10, votaveis: tuple[bytes, ...] = (),
            eleicoes: list | None = None, resultados_crus: bytes | None = None,
            fase: int = 2) -> bytes:
    """Um BU completo em DER, na estrutura que `bu.ler` espera por posição.

    `eleicoes` monta o caso de 2026, que não existe em nenhum boletim de 2024
    disponível: **duas eleições no mesmo arquivo** (federal e estadual), cada uma
    com vários cargos, no formato
    `[(código, comparecimento, [(cargo, votáveis), ...]), ...]`. O comparecimento
    fica no grupo e é compartilhado pelos cargos daquele grupo, como no BU real.
    """
    if eleicoes is None:
        eleicoes = [(eleicao, comparecimento, [(cargo, votaveis)])]

    blocos = []
    for codigo, comparecimento_da_eleicao, cargos in eleicoes:
        totais = [seq(ctx(0, codigo_cargo), inteiro(0), seq(*votos))
                  for codigo_cargo, votos in cargos]
        grupo = seq(inteiro(0), inteiro(comparecimento_da_eleicao), seq(*totais))
        blocos.append(seq(inteiro(codigo), inteiro(0), inteiro(0), inteiro(0), seq(grupo)))
    resultados = resultados_crus if resultados_crus is not None else seq(*blocos)

    cabecalho = seq(inteiro(1), ctx(1, pleito))
    identificacao = seq(seq(inteiro(municipio), inteiro(zona)),
                        inteiro(local), inteiro(secao))
    interno = seq(cabecalho, enumerado(fase), inteiro(0), identificacao,
                  inteiro(0), inteiro(0), inteiro(0), inteiro(0), resultados)
    return seq(inteiro(1), bytes_puros(interno))


# ----------------------------------------------------------------- der.py

class TestDer:
    def test_inteiro_com_sinal(self):
        no = der.ler(inteiro(-7))[0]
        assert no.inteiro == -7

    def test_tamanho_longo(self):
        """Mais de 127 bytes de conteúdo usa a forma longa de tamanho."""
        conteudo = b"x" * 300
        no = der.ler(bytes_puros(conteudo))[0]
        assert no.conteudo == conteudo

    def test_arvore_aninhada(self):
        no = der.ler(seq(inteiro(1), seq(inteiro(2), inteiro(3))))[0]
        assert no.construido
        assert [f.inteiro for f in no.filhos[1].filhos] == [2, 3]

    def test_etiqueta_de_contexto_e_reconhecida(self):
        no = der.ler(ctx(3, 42))[0]
        assert no.classe == der.CLASSE_CONTEXTO
        assert no.numero == 3


# ----------------------------------------------------------------- bu.py

class TestBoletimFabricado:
    def test_identificacao_da_secao(self):
        b = bu.ler(boletim(votaveis=(votavel(1, 10, 44, 44),)))
        assert b.municipio == "00310"
        assert (b.zona, b.local, b.secao) == (17, 1074, 1)
        assert b.id_local_votacao == "00310_17_1074"

    def test_municipio_com_zeros_a_esquerda(self):
        """O código do município tem cinco dígitos — é chave, não número."""
        b = bu.ler(boletim(municipio=1, comparecimento=1, votaveis=(votavel(1, 1, 44, 44),)))
        assert b.municipio == "00001"

    def test_tipos_de_voto(self):
        b = bu.ler(boletim(comparecimento=20, votaveis=(
            votavel(1, 10, 44, 44), votavel(4, 5, 44), votavel(2, 3), votavel(3, 2),
        )))
        cargo = b.eleicoes[619][0]
        assert [(v.tipo, v.quantidade) for v in cargo.votos] == [
            ("nominal", 10), ("legenda", 5), ("branco", 3), ("nulo", 2)
        ]

    def test_branco_e_nulo_nao_tem_numero(self):
        b = bu.ler(boletim(comparecimento=3, votaveis=(votavel(2, 3),)))
        voto = b.eleicoes[619][0].votos[0]
        assert voto.numero is None and voto.partido is None

    def test_conferencia_aceita_boletim_que_fecha(self):
        b = bu.ler(boletim(comparecimento=12, votaveis=(
            votavel(1, 10, 44, 44), votavel(2, 2),
        )))
        assert bu.conferir(b) == []

    def test_conferencia_recusa_soma_diferente_do_comparecimento(self):
        """A guarda que pega parser lendo errado: votos têm de somar comparecimento."""
        b = bu.ler(boletim(comparecimento=99, votaveis=(votavel(1, 10, 44, 44),)))
        problemas = bu.conferir(b)
        assert len(problemas) == 1
        assert "comparecimento" in problemas[0]

    def test_le_a_fase_do_boletim(self):
        """A urna gera boletim em simulado e em treinamento com a mesma
        estrutura do oficial. Contar um deles seria publicar voto inexistente."""
        assert bu.ler(boletim(votaveis=(votavel(1, 10, 44, 44),))).fase == "oficial"
        assert bu.ler(boletim(fase=1, votaveis=(votavel(1, 10, 44, 44),))).fase == "simulado"
        assert bu.ler(boletim(fase=3, votaveis=(votavel(1, 10, 44, 44),))).fase == "treinamento"

    def test_cargo_sem_candidato_nao_derruba_a_urna(self):
        """TipoVoto 5 da especificação: cargo que não teve candidato nenhum.

        Não foi descoberto lendo arquivos — veio do schema do TSE. Sem ele, a
        conferência acusaria tipo desconhecido e descartaria a urna inteira.
        """
        b = bu.ler(boletim(comparecimento=10, votaveis=(votavel(5, 10),)))
        voto = b.eleicoes[619][0].votos[0]
        assert voto.tipo == "cargo_sem_candidato"
        assert bu.conferir(b) == []

    def test_conferencia_recusa_tipo_de_voto_desconhecido(self):
        b = bu.ler(boletim(comparecimento=10, votaveis=(votavel(9, 10, 44, 44),)))
        assert any("desconhecido" in p for p in bu.conferir(b))

    def test_nao_e_bu(self):
        with pytest.raises(bu.BoletimInvalido):
            bu.ler(b"isto nao e um boletim")


class TestBoletimDeDoisTurnosDeEleicao:
    """O caso de 2026, que nenhum boletim de 2024 disponível exercita: a urna
    grava a eleição federal e a estadual no mesmo arquivo, com vários cargos.

    O parser trata isso por desenho — percorre as eleições montando um
    dicionário por código —, e é isto que prova que trata.
    """

    FEDERAL, ESTADUAL = 6257, 6259
    PRESIDENTE, DEP_FEDERAL, SENADOR = 1, 6, 5
    GOVERNADOR, DEP_ESTADUAL = 3, 7

    def montar(self):
        return bu.ler(boletim(eleicoes=[
            (self.FEDERAL, 300, [
                (self.PRESIDENTE, (votavel(1, 200, 13, 13), votavel(2, 60), votavel(3, 40))),
                (self.DEP_FEDERAL, (votavel(1, 250, 55, 5555), votavel(4, 20, 55),
                                    votavel(3, 30))),
                (self.SENADOR, (votavel(1, 280, 40, 400), votavel(2, 20))),
            ]),
            (self.ESTADUAL, 300, [
                (self.GOVERNADOR, (votavel(1, 290, 22, 22), votavel(3, 10))),
                (self.DEP_ESTADUAL, (votavel(1, 300, 10, 10111),)),
            ]),
        ]))

    def test_as_duas_eleicoes_aparecem(self):
        b = self.montar()
        assert sorted(b.eleicoes) == [self.FEDERAL, self.ESTADUAL]

    def test_os_cargos_de_cada_eleicao(self):
        b = self.montar()
        assert sorted(c.codigo for c in b.eleicoes[self.FEDERAL]) == [1, 5, 6]
        assert sorted(c.codigo for c in b.eleicoes[self.ESTADUAL]) == [3, 7]

    def test_cada_cargo_fecha_o_proprio_comparecimento(self):
        assert bu.conferir(self.montar()) == []

    def test_legenda_de_deputado_federal(self):
        b = self.montar()
        federal = {c.codigo: c for c in b.eleicoes[self.FEDERAL]}
        legenda = [v for v in federal[self.DEP_FEDERAL].votos if v.tipo == "legenda"]
        assert [(v.partido, v.numero, v.quantidade) for v in legenda] == [(55, 55, 20)]

    def test_o_conversor_separa_a_eleicao_pedida(self, tmp_path):
        """Domingo roda com --eleicao 6257: a estadual tem de ficar de fora."""
        caminho = tmp_path / "XX.zip"
        import zipfile
        with zipfile.ZipFile(caminho, "w") as z:
            z.writestr("00310/0017/0001.bu", boletim(eleicoes=[
                (self.FEDERAL, 10, [(self.PRESIDENTE, (votavel(1, 10, 13, 13),))]),
                (self.ESTADUAL, 10, [(self.GOVERNADOR, (votavel(1, 10, 22, 22),))]),
            ]))
        contagem, resumo = converter.ler_bus(caminho, self.FEDERAL, {1, 6})
        assert resumo["bus"] == 1 and resumo["recusados"] == 0
        assert list(contagem.values()) == [10]
        assert {chave[3] for chave in contagem} == {1}   # só presidente


class TestFormatoOficialDoTse:
    """Os boletins de exemplo que acompanham a especificação ASN.1 do TSE.

    Valem mais do que parecem. O formato do BU **mudou entre 2022 e 2024**: com a
    especificação publicada em 2022, o próprio leitor oficial do TSE não lê um
    boletim de 2024 (erra em `Carga.dataHoraCarga`), e a estrutura de
    `ResultadoVotacaoPorEleicao` passou de 3 para 7 campos.

    Estes arquivos são do formato de 2022 e trazem **duas eleições no mesmo
    boletim, com cinco cargos** — que é a forma que 2026 terá. Ler estes e os de
    2024 com o mesmo código é a melhor evidência disponível de que a leitura
    sobrevive a uma mudança de formato.
    """

    FEDERAL = Path(__file__).resolve().parent / "fixtures" / "bu_exemplo_tse_2022_federal.bu"
    MUNICIPAL = Path(__file__).resolve().parent / "fixtures" / "bu_exemplo_tse_2022_municipal.bu"

    @pytest.mark.skipif(not FEDERAL.exists(), reason="fixture ausente")
    def test_le_o_exemplo_federal_com_duas_eleicoes(self):
        b = bu.ler(self.FEDERAL.read_bytes())
        assert sorted(b.eleicoes) == [2101, 2102]
        cargos = sorted(c.codigo for el in b.eleicoes.values() for c in el)
        assert cargos == [1, 3, 5, 6, 7]       # presidente, governador, senador, dep. fed e est.
        assert b.id_local_votacao == "01392_9_4"

    @pytest.mark.skipif(not FEDERAL.exists(), reason="fixture ausente")
    def test_o_exemplo_federal_fecha_a_propria_conta(self):
        assert bu.conferir(bu.ler(self.FEDERAL.read_bytes())) == []

    @pytest.mark.skipif(not MUNICIPAL.exists(), reason="fixture ausente")
    def test_le_o_exemplo_municipal(self):
        b = bu.ler(self.MUNICIPAL.read_bytes())
        assert sorted(c.codigo for el in b.eleicoes.values() for c in el) == [11, 13]
        assert bu.conferir(b) == []

    @pytest.mark.skipif(not (FEDERAL.exists() and BU_REAL.exists()), reason="fixtures ausentes")
    def test_o_mesmo_codigo_le_os_dois_formatos(self):
        """2022 e 2024 têm layouts diferentes; a leitura não depende disso."""
        de_2022 = bu.ler(self.FEDERAL.read_bytes())
        de_2024 = bu.ler(BU_REAL.read_bytes())
        assert de_2022.eleicoes and de_2024.eleicoes
        assert bu.conferir(de_2022) == [] and bu.conferir(de_2024) == []


class TestSenadoComDuasVagas:
    """O caso que derrubou a camada ao vivo na noite de 2026.

    O Senado renova um terço ou dois terços das cadeiras. Em 2026 foram dois
    terços: cada eleitor votou em dois senadores, e a soma do cargo deu o dobro
    do comparecimento. A conferência exigia igualdade, reprovava o boletim
    inteiro — e junto com ele o resultado de presidente, que estava correto.
    Como ela é a porta tanto da coleta em lote quanto da camada ao vivo, isso
    significava nenhum resultado em lugar nenhum do país.
    """

    DE_2026 = Path(__file__).resolve().parent / "fixtures" / "bu_exemplo_2026_federal.bu"

    @pytest.mark.skipif(not DE_2026.exists(), reason="fixture ausente")
    def test_boletim_real_de_2026_fecha_a_conta(self):
        b = bu.ler(self.DE_2026.read_bytes())
        senador = [c for el in b.eleicoes.values() for c in el if c.codigo == 5]
        assert senador, "a fixture precisa ter o cargo de senador"
        assert senador[0].total == senador[0].comparecimento * 2
        assert bu.conferir(b) == []

    @staticmethod
    def com_um_cargo(codigo: int, comparecimento: int, votos: int) -> bu.Boletim:
        """Um boletim de um cargo só, para olhar a aritmética sem passar por DER."""
        return bu.Boletim(
            municipio="49956", zona=291, local=1040, secao=22, pleito=3220,
            fase="oficial",
            eleicoes={6259: [bu.Cargo(codigo, comparecimento,
                                      [bu.Voto("nominal", votos, None, 123)])]},
        )

    def test_dobro_so_vale_para_senador(self):
        """Presidente com o dobro de votos é erro de leitura, não eleição."""
        assert any("cargo 1" in p
                   for p in bu.conferir(self.com_um_cargo(1, 100, 200)))

    def test_senador_com_o_triplo_continua_sendo_erro(self):
        assert any("cargo 5" in p
                   for p in bu.conferir(self.com_um_cargo(5, 100, 300)))

    def test_senador_com_uma_vaga_tambem_passa(self):
        """2022 renovou um terço: um voto por eleitor, e isso segue válido."""
        assert bu.conferir(self.com_um_cargo(5, 100, 100)) == []

    def test_senador_com_duas_vagas_passa(self):
        assert bu.conferir(self.com_um_cargo(5, 100, 200)) == []


class TestRecusaDeEstrutura:
    """Uma mudança de formato tem de virar recusa contada, nunca número errado.

    Não há como testar o BU de 2026 antes de domingo — o servidor do TSE só
    mantém os ciclos correntes, e o ambiente de simulado está fora. O que se
    pode garantir é o comportamento quando a estrutura não é a esperada.
    """

    def recusa(self, dados: bytes) -> str:
        with pytest.raises(bu.BoletimInvalido) as erro:
            bu.ler(dados)
        return str(erro.value)

    def test_envelope_sem_campos(self):
        assert "envelope" in self.recusa(seq())

    def test_boletim_truncado_no_meio(self):
        bom = boletim(votaveis=(votavel(1, 10, 44, 44),))
        assert self.recusa(bom[: len(bom) // 2])

    def test_sem_o_bloco_de_resultados(self):
        """O bloco de resultados é o nono campo; um boletim com oito não serve."""
        curto = seq(seq(inteiro(1), ctx(1, 452)), inteiro(0), inteiro(0),
                    seq(seq(inteiro(310), inteiro(17)), inteiro(1074), inteiro(1)),
                    inteiro(0), inteiro(0), inteiro(0), inteiro(0))
        assert "resultados" in self.recusa(seq(inteiro(1), bytes_puros(curto)))

    def test_identificacao_com_formato_diferente(self):
        torto = seq(seq(inteiro(1), ctx(1, 452)), inteiro(0), inteiro(0),
                    inteiro(999),      # devia ser a identificação da seção
                    inteiro(0), inteiro(0), inteiro(0), inteiro(0),
                    seq(seq(inteiro(619), inteiro(0), inteiro(0), inteiro(0),
                            seq(seq(inteiro(0), inteiro(1),
                                    seq(seq(ctx(0, 11), inteiro(0),
                                            seq(votavel(1, 1, 44, 44)))))))))
        assert "município e zona" in self.recusa(seq(inteiro(1), bytes_puros(torto)))

    def test_nenhuma_eleicao(self):
        assert "eleição" in self.recusa(boletim(resultados_crus=seq()))

    def test_cargo_sem_votavel(self):
        """Sem votável nenhum, nada no boletim tem a forma de resultado."""
        assert "forma esperada" in self.recusa(boletim(votaveis=()))

    def test_codigo_de_cargo_onde_esperava_inteiro_cru(self):
        """Se o campo do cargo virar um nó com filhos, recusa em vez de inventar."""
        resultados = seq(seq(inteiro(619), inteiro(0), inteiro(0), inteiro(0),
                             seq(seq(inteiro(0), inteiro(10),
                                     seq(seq(seq(inteiro(11)), inteiro(0),
                                             seq(votavel(1, 10, 44, 44))))))))
        assert self.recusa(boletim(resultados_crus=resultados))


@pytest.fixture(scope="module")
def b():
    return bu.ler(BU_REAL.read_bytes())


@pytest.mark.skipif(not BU_REAL.exists(), reason="fixture do BU real ausente")
class TestBoletimReal:
    """O boletim publicado de Alta Floresta/RO, seção 1 da zona 17, em 2024."""

    def test_identificacao(self, b):
        assert b.id_local_votacao == "00310_17_1074"
        assert (b.secao, b.pleito) == (1, 452)

    def test_duas_eleicoes_no_mesmo_arquivo(self, b):
        """Uma urna municipal grava prefeito e vereador no mesmo BU."""
        assert sorted(c.codigo for c in b.eleicoes[619]) == [11, 13]

    def test_fecha_com_o_proprio_comparecimento(self, b):
        assert bu.conferir(b) == []
        for cargo in b.eleicoes[619]:
            assert cargo.total == cargo.comparecimento == 176

    def test_prefeito_conhecido(self, b):
        prefeito = next(c for c in b.eleicoes[619] if c.codigo == 11)
        nominais = {v.numero: v.quantidade for v in prefeito.votos if v.tipo == "nominal"}
        assert nominais == {13: 7, 22: 45, 44: 118}


# ----------------------------------------------------------------- converter.py

def cadastro(linhas: list[dict]) -> pd.DataFrame:
    colunas = ["SG_UF", "DS_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO", "SG_UE",
               "NM_URNA_CANDIDATO", "NR_PARTIDO", "SG_PARTIDO",
               "DS_SITUACAO_CANDIDATURA", "DS_SIT_TOT_TURNO"]
    cad = pd.DataFrame(linhas, columns=colunas).astype(str)
    cad["cargo"] = cad["DS_CARGO"].str.upper().str.strip()
    return cad


def candidato(numero, nome, ue, cargo="PREFEITO", sq="1", partido="44",
              situacao="APTO", totalizacao="Eleito", uf="RR") -> dict:
    return {"SG_UF": uf, "DS_CARGO": cargo, "SQ_CANDIDATO": sq, "NR_CANDIDATO": numero,
            "SG_UE": ue, "NM_URNA_CANDIDATO": nome, "NR_PARTIDO": partido,
            "SG_PARTIDO": "PARTIDO", "DS_SITUACAO_CANDIDATURA": situacao,
            "DS_SIT_TOT_TURNO": totalizacao}


class TestLerBus:
    """A política de recusa: urna com defeito é contada, formato diferente para tudo."""

    def zip_com(self, tmp_path, bons: int, ruins: int):
        import zipfile
        caminho = tmp_path / "XX.zip"
        bom = boletim(comparecimento=10, votaveis=(votavel(1, 10, 44, 44),))
        with zipfile.ZipFile(caminho, "w") as z:
            for i in range(bons):
                z.writestr(f"00310/0017/{i:04d}.bu", bom)
            for i in range(ruins):
                z.writestr(f"00310/0018/{i:04d}.bu", b"nao e um boletim")
        return caminho

    def test_recusa_isolada_e_contada_e_segue(self, tmp_path):
        contagem, resumo = converter.ler_bus(self.zip_com(tmp_path, 300, 1), 619, {11})
        assert resumo["bus"] == 301
        assert resumo["recusados"] == 1
        assert sum(contagem.values()) == 3_000      # os 300 boletins bons entraram

    def test_recusa_em_massa_interrompe(self, tmp_path):
        with pytest.raises(SystemExit, match="acima do limite"):
            converter.ler_bus(self.zip_com(tmp_path, 0, 10), 619, {11})

    def test_curto_circuito_na_amostra_inicial(self, tmp_path):
        """Não vale varrer meio milhão de arquivos para descobrir no fim."""
        with pytest.raises(SystemExit, match="de 200 boletins"):
            converter.ler_bus(self.zip_com(tmp_path, 0, 400), 619, {11})

    def test_boletim_de_simulado_nao_entra_na_conta(self, tmp_path):
        """O arquivo de simulado tem a mesma estrutura do oficial e fecha a
        própria conta — só a fase o distingue."""
        import zipfile
        caminho = tmp_path / "XX.zip"
        bom = boletim(comparecimento=10, votaveis=(votavel(1, 10, 44, 44),))
        with zipfile.ZipFile(caminho, "w") as z:
            for i in range(300):
                z.writestr(f"00310/0017/{i:04d}.bu", bom)
            z.writestr("00310/0018/0001.bu",
                       boletim(fase=1, comparecimento=10, votaveis=(votavel(1, 10, 44, 44),)))
        contagem, resumo = converter.ler_bus(caminho, 619, {11})
        assert resumo["recusados"] == 1
        assert sum(contagem.values()) == 3_000      # só os 300 oficiais

    def test_boletim_que_nao_fecha_a_conta_tambem_e_recusado(self, tmp_path):
        import zipfile
        caminho = tmp_path / "XX.zip"
        bom = boletim(comparecimento=10, votaveis=(votavel(1, 10, 44, 44),))
        with zipfile.ZipFile(caminho, "w") as z:
            for i in range(300):
                z.writestr(f"00310/0017/{i:04d}.bu", bom)
            z.writestr("00310/0018/0001.bu",
                       boletim(comparecimento=99, votaveis=(votavel(1, 10, 44, 44),)))
        _, resumo = converter.ler_bus(caminho, 619, {11})
        assert resumo["problemas"] == 1 and resumo["recusados"] == 0


class TestRelatarForaDaMalha:
    """O voto em local que a malha não conhece é perdido na junção do passo 22.

    O conversor não conserta — diz o tamanho antes de três horas de pipeline.
    Mover voto para outro prédio exigiria confiar no cadastro de locais, que no
    ensaio de 2024 discorda da urna em 6,5% das seções.
    """

    def tabela(self, locais: list[tuple[str, int]]) -> pd.DataFrame:
        return pd.DataFrame([{"id_local_votacao": local, "qt_votos": votos}
                             for local, votos in locais])

    def de_para(self, tmp_path, locais: list[str]) -> Path:
        caminho = tmp_path / "de_para.parquet"
        pd.DataFrame([{"id_local_votacao": local, "id_regiao": i}
                      for i, local in enumerate(locais)]).to_parquet(caminho)
        return caminho

    def test_tudo_na_malha_nao_perde_nada(self, tmp_path, capsys):
        resumo = converter.relatar_fora_da_malha(
            self.tabela([("RR_03018_1_1015", 100)]),
            self.de_para(tmp_path, ["RR_03018_1_1015"]))
        assert resumo == {"votos_fora": 0, "locais_fora": 0}
        assert "fora da malha" not in capsys.readouterr().out

    def test_conta_voto_e_local_fora(self, tmp_path, capsys):
        resumo = converter.relatar_fora_da_malha(
            self.tabela([("RR_03018_1_1015", 100), ("RR_03018_1_9999", 30),
                         ("RR_03018_1_8888", 20)]),
            self.de_para(tmp_path, ["RR_03018_1_1015"]))
        assert resumo == {"votos_fora": 50, "locais_fora": 2}
        assert "33.33%" in capsys.readouterr().out

    def test_sem_de_para_nao_quebra(self, tmp_path):
        assert converter.relatar_fora_da_malha(
            self.tabela([("RR_03018_1_1015", 100)]), tmp_path / "nao_existe.parquet") == {}


class TestEscolherCandidatura:
    def test_sem_repeticao_devolve_tudo(self):
        cad = cadastro([candidato("44", "A", "03018"), candidato("15", "B", "03018")])
        assert len(converter.escolher_candidatura(cad)) == 2

    def test_prefere_quem_tem_situacao_de_totalizacao(self):
        """Substituição de candidato: duas inscrições, um número, uma só concorreu."""
        cad = cadastro([
            candidato("44", "NICOLETTI", "03018", sq="9", totalizacao="#NULO"),
            candidato("44", "CATARINA", "03018", sq="1", totalizacao="Não eleito"),
        ])
        escolhida = converter.escolher_candidatura(cad)
        assert list(escolhida["NM_URNA_CANDIDATO"]) == ["CATARINA"]

    def test_depois_prefere_a_apta(self):
        cad = cadastro([
            candidato("44", "INAPTA", "03018", sq="9", situacao="INAPTO", totalizacao="#NULO"),
            candidato("44", "APTA", "03018", sq="1", situacao="APTO", totalizacao="#NE"),
        ])
        assert list(converter.escolher_candidatura(cad)["NM_URNA_CANDIDATO"]) == ["APTA"]

    def test_mesmo_numero_em_municipios_diferentes_nao_e_repeticao(self):
        """A unidade eleitoral faz parte da chave: nº 44 existe em cada município."""
        cad = cadastro([candidato("44", "DAQUI", "03018"), candidato("44", "DALI", "03131")])
        escolhida = converter.escolher_candidatura(cad)
        assert set(escolhida["NM_URNA_CANDIDATO"]) == {"DAQUI", "DALI"}


class TestMontarTabela:
    def contagem(self, **extra):
        # (município, zona, local, cargo, tipo, partido, número) → votos
        base = {("03018", 1, 1015, 11, "nominal", 44, 44): 100}
        base.update(extra)
        return base

    def test_esquema_igual_ao_do_passo_21(self):
        cad = cadastro([candidato("44", "CATARINA", "03018")])
        df = converter.montar_tabela(self.contagem(), "RR", 2024, 1, cad)
        assert list(df.columns) == [
            "id_local_votacao", "sg_uf", "ano_eleicao", "turno", "cargo", "sq_candidato",
            "nr_votavel", "nm_votavel", "sg_partido", "tipo_voto", "qt_votos",
        ]
        assert df.loc[0, "id_local_votacao"] == "RR_03018_1_1015"
        assert df.loc[0, "nm_votavel"] == "CATARINA"

    def test_candidato_vem_do_municipio_certo(self):
        """O erro que este teste existe para impedir: nome certo em cima do
        número certo. Ligar só por UF traria o candidato do município vizinho."""
        cad = cadastro([candidato("44", "DAQUI", "03018"), candidato("44", "DALI", "03131")])
        contagem = {("03018", 1, 1015, 11, "nominal", 44, 44): 100,
                    ("03131", 2, 2020, 11, "nominal", 44, 44): 50}
        df = converter.montar_tabela(contagem, "RR", 2024, 1, cad)
        nomes = dict(zip(df["id_local_votacao"], df["nm_votavel"]))
        assert nomes == {"RR_03018_1_1015": "DAQUI", "RR_03131_2_2020": "DALI"}

    def test_presidente_usa_unidade_br(self):
        cad = cadastro([candidato("13", "ALGUEM", "BR", cargo="PRESIDENTE")])
        contagem = {("03018", 1, 1015, 1, "nominal", 13, 13): 7}
        df = converter.montar_tabela(contagem, "RR", 2026, 1, cad)
        assert df.loc[0, "nm_votavel"] == "ALGUEM"
        assert df.loc[0, "cargo"] == "PRESIDENTE"

    def test_convencoes_de_branco_nulo_e_legenda(self):
        cad = cadastro([candidato("44", "CATARINA", "03018", cargo="VEREADOR")])
        contagem = {
            ("03018", 1, 1015, 13, "branco", None, None): 3,
            ("03018", 1, 1015, 13, "nulo", None, None): 2,
            ("03018", 1, 1015, 13, "legenda", 44, None): 5,
        }
        df = converter.montar_tabela(contagem, "RR", 2024, 1, cad).set_index("tipo_voto")
        assert (df.loc["branco", "nr_votavel"], df.loc["branco", "sq_candidato"]) == ("95", "-1")
        assert (df.loc["nulo", "nr_votavel"], df.loc["nulo", "sq_candidato"]) == ("96", "-1")
        assert df.loc["legenda", "sq_candidato"] == converter.SQ_LEGENDA
        assert df.loc["legenda", "nm_votavel"] == "PARTIDO"
        assert pd.isna(df.loc["branco", "sg_partido"])


class TestMesclarNaBase:
    def tabela(self, ano: int, votos: int) -> pd.DataFrame:
        return pd.DataFrame([{
            "id_local_votacao": "RR_03018_1_1015", "sg_uf": "RR", "ano_eleicao": ano,
            "turno": "1", "cargo": "PRESIDENTE", "sq_candidato": "1", "nr_votavel": "13",
            "nm_votavel": "ALGUEM", "sg_partido": "PARTIDO", "tipo_voto": "nominal",
            "qt_votos": votos,
        }])

    def test_cria_quando_nao_existe(self, tmp_path):
        destino = tmp_path / "votos.parquet"
        converter.mesclar_na_base(self.tabela(2026, 10), destino, 2026)
        assert len(pd.read_parquet(destino)) == 1

    def test_preserva_os_outros_anos(self, tmp_path):
        destino = tmp_path / "votos.parquet"
        self.tabela(2022, 7).to_parquet(destino, index=False)
        converter.mesclar_na_base(self.tabela(2026, 10), destino, 2026)
        assert sorted(pd.read_parquet(destino)["ano_eleicao"]) == [2022, 2026]

    def test_rodar_duas_vezes_nao_duplica(self, tmp_path):
        """Idempotência: domingo o conversor pode rodar mais de uma vez."""
        destino = tmp_path / "votos.parquet"
        for _ in range(3):
            converter.mesclar_na_base(self.tabela(2026, 10), destino, 2026)
        base = pd.read_parquet(destino)
        assert len(base) == 1 and base.loc[0, "qt_votos"] == 10

    def test_recusa_esquema_diferente(self, tmp_path):
        """Mesclar com esquema trocado corromperia a base do pipeline."""
        destino = tmp_path / "votos.parquet"
        self.tabela(2022, 7).drop(columns=["sg_partido"]).to_parquet(destino, index=False)
        with pytest.raises(SystemExit, match="esquema diferente"):
            converter.mesclar_na_base(self.tabela(2026, 10), destino, 2026)


# ----------------------------------------------------------------- conferir.py

def oficial(vnom=0, vl=0, van=0, vansj=0, vb=0, vn=0, vnt=0, candidatos=(), legendas=()):
    """O bloco de totais como o arquivo do TSE o publica (ver `conferir`)."""
    vvc = vnom + vl + van + vansj
    tvn = vn + vnt
    return {
        "v": {"tv": str(vvc + vb + tvn), "vvc": str(vvc), "vv": str(vnom + vl),
              "vnom": str(vnom), "vl": str(vl), "van": str(van), "vansj": str(vansj),
              "vb": str(vb), "tvn": str(tvn), "vn": str(vn), "vnt": str(vnt),
              "pvap": "ignorar"},
        "e": {"c": str(vvc + vb + tvn)},
        "carg": [{"agr": [{"par": [
            {"n": numero_partido, "sg": sigla, "tvtl": str(votos_legenda),
             "cand": [{"n": n, "nmu": nome, "vap": str(v)} for n, nome, v in cands]}
            for numero_partido, sigla, votos_legenda, cands in _partidos(candidatos, legendas)
        ]}]}],
    }


def _partidos(candidatos, legendas):
    """Agrupa candidatos e legenda sob o partido, como o JSON do TSE faz."""
    saida = []
    for numero, sigla, votos in legendas:
        saida.append((numero, sigla, votos,
                      [c for c in candidatos if str(c[0]).startswith(str(numero))]))
    sob_legenda = {str(c[0]) for _, _, _, cs in saida for c in cs}
    soltos = [c for c in candidatos if str(c[0]) not in sob_legenda]
    if soltos:
        saida.append(("00", "SEM LEGENDA", 0, soltos))
    return saida


def nossos(nominal=(), legenda=(), brancos=0, nulos=0):
    linhas = [{"tipo_voto": "nominal", "nr_votavel": str(n), "qt_votos": v}
              for n, v in nominal]
    linhas += [{"tipo_voto": "legenda", "nr_votavel": str(n), "qt_votos": v}
               for n, v in legenda]
    if brancos:
        linhas.append({"tipo_voto": "branco", "nr_votavel": "95", "qt_votos": brancos})
    if nulos:
        linhas.append({"tipo_voto": "nulo", "nr_votavel": "96", "qt_votos": nulos})
    return pd.DataFrame(linhas)


class TestTotaisDoOficial:
    def test_caso_simples(self):
        totais, _, avisos, _ = conferir.totais_do_oficial(oficial(vnom=100, vb=5, vn=3))
        assert avisos == []
        assert totais == {"comparecimento": 108, "validos": 100, "brancos": 5, "nulos": 3}

    def test_anulado_sub_judice_conta_como_valido_para_nos(self):
        """São João da Baliza/RR, 2024: `vnom` 4.009 exclui os 836 do nº 15, que
        estão em `vansj`. A urna gravou nominal; a totalização reclassificou."""
        totais, ajustes, _, _ = conferir.totais_do_oficial(
            oficial(vnom=4009, vansj=836, vb=91, vn=119))
        assert totais["validos"] == 4845
        assert totais["nulos"] == 119
        assert ajustes["vansj"] == 836

    def test_nominal_virado_nulo_volta_para_validos(self):
        """`vnt` é voto que a urna gravou nominal e a totalização pôs em nulo."""
        totais, _, _, _ = conferir.totais_do_oficial(oficial(vnom=6405, vb=28, vn=108, vnt=1))
        assert totais["validos"] == 6406
        assert totais["nulos"] == 108

    def test_legenda_entra_nos_validos(self):
        totais, _, _, _ = conferir.totais_do_oficial(
            oficial(vnom=173490, vl=3308, vansj=1039, vb=3176, vn=2889, vnt=9))
        assert totais["validos"] == 177846
        assert totais["nulos"] == 2889

    def test_avisa_quando_a_identidade_do_arquivo_nao_fecha(self):
        conteudo = oficial(vnom=100, vb=5, vn=3)
        conteudo["v"]["tv"] = "999"
        _, _, avisos, _ = conferir.totais_do_oficial(conteudo)
        assert any("não fecha" in a for a in avisos)


class TestVotaveisDoOficial:
    def test_separa_candidato_de_legenda(self):
        nominais, legendas = conferir.votaveis_do_oficial(oficial(
            vnom=150, vl=20, candidatos=[("1011", "ALGUEM", 150)],
            legendas=[("10", "PARTIDO", 20)]))
        assert nominais == {"1011": ("ALGUEM", 150)}
        assert legendas == {"10": ("PARTIDO (legenda)", 20)}

    def test_partido_sem_legenda_fica_de_fora(self):
        _, legendas = conferir.votaveis_do_oficial(oficial(
            vnom=150, candidatos=[("1011", "ALGUEM", 150)],
            legendas=[("10", "PARTIDO", 0)]))
        assert legendas == {}


class TestConferirTotais:
    def compara(self, df, conteudo):
        deles, ajustes, _, _ = conferir.totais_do_oficial(conteudo)
        _, _, nossos_totais = conferir.nossos_numeros(df)
        return conferir.conferir_totais(nossos_totais, deles, ajustes, "teste")

    def test_igual_nao_acusa_nada(self):
        conteudo = oficial(vnom=100, vb=5, vn=3)
        assert self.compara(nossos(nominal=[("13", 100)], brancos=5, nulos=3), conteudo) == []

    def test_reclassificacao_do_oficial_nao_acusa_nada(self):
        """O caso que antes passava como 'tolerância' e agora fecha em zero."""
        conteudo = oficial(vnom=4009, vansj=836, vb=91, vn=119)
        nosso = nossos(nominal=[("11", 3852), ("15", 836), ("50", 157)], brancos=91, nulos=119)
        assert self.compara(nosso, conteudo) == []

    def test_legenda_somada_aos_validos(self):
        conteudo = oficial(vnom=100, vl=20, vb=5, vn=3)
        nosso = nossos(nominal=[("1011", 100)], legenda=[("10", 20)], brancos=5, nulos=3)
        assert self.compara(nosso, conteudo) == []

    def test_secao_faltando_dentro_do_limite_passa_com_aviso(self):
        """Uma urna sem BU transmitido tira votos de todas as prateleiras."""
        conteudo = oficial(vnom=100_000, vb=5_000, vn=3_000)
        nosso = nossos(nominal=[("13", 99_970)], brancos=5_000, nulos=3_000)
        assert self.compara(nosso, conteudo) == []

    def test_secao_faltando_acima_do_limite_barra(self):
        conteudo = oficial(vnom=100_000, vb=5_000, vn=3_000)
        nosso = nossos(nominal=[("13", 95_000)], brancos=5_000, nulos=3_000)
        problemas = self.compara(nosso, conteudo)
        assert any("acima do limite" in p for p in problemas)

    def test_votos_a_mais_que_o_oficial_barra(self):
        conteudo = oficial(vnom=100, vb=5, vn=3)
        nosso = nossos(nominal=[("13", 200)], brancos=5, nulos=3)
        problemas = self.compara(nosso, conteudo)
        assert any("a mais que o oficial" in p for p in problemas)

    def test_troca_entre_prateleiras_barra(self):
        """Comparecimento certo e nulos virando válidos: erro de leitura de tipo."""
        conteudo = oficial(vnom=100, vb=5, vn=3)
        nosso = nossos(nominal=[("13", 103)], brancos=5)
        problemas = self.compara(nosso, conteudo)
        assert any("validos" in p for p in problemas)
        assert any("nulos" in p for p in problemas)


class TestCompararVotaveis:
    def test_diferenca_relevante_e_acusada(self):
        problemas = conferir.comparar_votaveis(
            {"13": 1300}, {"13": ("ALGUEM", 1000)}, "teste", "por candidato")
        assert len(problemas) == 1 and "+300" in problemas[0]

    def test_votavel_que_o_oficial_nao_tem_e_acusado(self):
        problemas = conferir.comparar_votaveis(
            {"99": 500}, {"13": ("ALGUEM", 1000)}, "teste", "por candidato")
        assert any("nenhum no oficial" in p for p in problemas)
        assert any("nº 13" in p for p in problemas)   # e o que falta também

    def test_diferenca_minuscula_cabe_no_colchao(self):
        assert conferir.comparar_votaveis(
            {"13": 1001}, {"13": ("ALGUEM", 1000)}, "teste", "por candidato") == []


# ----------------------------------------------------------------- tse.py

def config_secoes(secoes: list[dict]) -> dict:
    return {"abr": [{"cd": "RR", "mu": [{"cd": "03018", "nm": "BOA VISTA",
                                         "zon": [{"cd": "0001", "sec": secoes}]}]}]}


class TestTse:
    def test_sufixos_com_seis_digitos(self):
        eleicao = tse.Eleicao(codigo="6257", nome="", turno="1", ciclo="ele2026",
                              pleito="3220", data="")
        assert eleicao.sufixo_eleicao == "e006257"
        assert eleicao.sufixo_pleito == "p003220"

    def test_secao_agregada_fica_de_fora(self):
        """Agregada não tem urna: pedir arquivo dela daria 404, e 404 arrisca bloqueio."""
        secoes = tse.secoes_da_uf(config_secoes([
            {"ns": "0001"}, {"ns": "0002", "nsp": "0001"}, {"ns": "0003"},
        ]))
        assert [s["secao"] for s in secoes] == ["0001", "0003"]

    def test_so_com_aux_fica_com_o_que_ja_foi_transmitido(self):
        """O EA16 só preenche `da`/`ha` depois de gerar o arquivo auxiliar. É por
        isso que a coleta pode começar durante a apuração sem produzir 404."""
        config = config_secoes([
            {"ns": "0001", "da": "04/10/2026", "ha": "17:09:29"},
            {"ns": "0002"},
            {"ns": "0003", "da": "04/10/2026", "ha": "18:22:10"},
            {"ns": "0004", "nsp": "0001", "da": "04/10/2026"},
        ])
        assert [s["secao"] for s in tse.secoes_da_uf(config)] == ["0001", "0002", "0003"]
        assert [s["secao"] for s in tse.secoes_da_uf(config, so_com_aux=True)] == ["0001", "0003"]

    def test_secoes_trazem_o_que_o_coletor_precisa(self):
        secao = tse.secoes_da_uf(config_secoes([{"ns": "0001"}]))[0]
        assert secao["uf"] == "RR" and secao["municipio"] == "03018"
        assert secao["zona"] == "0001" and secao["secao"] == "0001"

    def test_caminhos_saem_da_configuracao_nao_do_palpite(self):
        eleicao = tse.Eleicao(codigo="6257", nome="", turno="1", ciclo="ele2026",
                              pleito="3220", data="")
        cliente = tse.Cliente()
        assert cliente.caminho_config_secoes(eleicao, "RR") == (
            "oficial/ele2026/arquivo-urna/3220/config/rr/rr-p003220-cs.json")
        assert cliente.caminho_aux_secao(eleicao, "RR", "03018", "0001", "0002") == (
            "oficial/ele2026/arquivo-urna/3220/dados/rr/03018/0001/0002"
            "/p003220-rr-m03018-z0001-s0002-aux.json")

    def test_limite_de_taxa_respeita_o_teto(self):
        """O limitador é o que impede o bloqueio de 10 minutos do TSE."""
        import time
        limite = tse.LimiteDeTaxa(por_segundo=5)
        inicio = time.monotonic()
        for _ in range(11):
            limite.aguardar()
        assert time.monotonic() - inicio >= 2.0

    def test_eleicao_federal_sai_do_arquivo_de_configuracao(self, monkeypatch):
        config = {"pl": [
            {"c": "ele2026", "cd": "3220", "dt": "04/10/2026", "e": [
                {"cd": 6257, "nm": "Eleição Ordinária Federal - 2026 1º Turno", "t": 1},
                {"cd": 6259, "nm": "Eleição Ordinária Estadual - 2026 1º Turno", "t": 1},
                {"cd": 6261, "nm": "Eleição Ordinária Municipal - 2026 1º Turno", "t": 1},
            ]},
        ]}
        cliente = tse.Cliente()
        monkeypatch.setattr(cliente, "json", lambda *a, **k: config)
        assert cliente.eleicao_federal(2026, 1).codigo == "6257"

    def test_eleicao_federal_reclama_se_nao_achar(self, monkeypatch):
        cliente = tse.Cliente()
        monkeypatch.setattr(cliente, "json", lambda *a, **k: {"pl": []})
        with pytest.raises(LookupError):
            cliente.eleicao_federal(2026, 1)


class TestColetor:
    def test_o_exterior_entra_na_coleta(self):
        """Sem `zz` faltariam uns 300 mil votos de presidente no total do Brasil,
        e a conferência nacional barraria a publicação com razão."""
        assert len(coletar.UFS) == 28
        assert "zz" in coletar.UFS

    def test_hash_totalizado_ganha_do_ultimo_recebido(self):
        """Seção que retransmitiu: o último hash pode ser o rejeitado. Vale o que
        entrou no resultado, e o EA18 marca qual é."""
        aux = {"st": "Totalizada", "hashes": [
            {"hash": "aaa", "st": "Totalizado"},
            {"hash": "bbb", "st": "Rejeitado"},
        ]}
        assert coletar.escolher_hash(aux)["hash"] == "aaa"

    def test_antes_da_totalizacao_vale_o_ultimo_recebido(self):
        aux = {"st": "Recebida", "hashes": [
            {"hash": "aaa", "st": "Recebido"},
            {"hash": "bbb", "st": "Recebido"},
        ]}
        assert coletar.escolher_hash(aux)["hash"] == "bbb"

    def test_secao_sem_hash(self):
        assert coletar.escolher_hash({"st": "Não instalada", "hashes": []}) is None
        assert coletar.escolher_hash({"st": "Não instalada"}) is None

    def test_nome_no_zip_identifica_a_secao(self):
        nome = coletar.nome_no_zip({"municipio": "03018", "zona": "0001", "secao": "0002"})
        assert nome == "03018/0001/0002.bu"


# ----------------------------------------------------------------- domingo.py

class TestTrocarAnoDeReferencia:
    """A única linha de configuração que a noite muda. Editar no escuro um
    arquivo que o pipeline inteiro lê seria a pior forma de errar."""

    def config(self, tmp_path, texto):
        caminho = tmp_path / "config.py"
        caminho.write_text(texto, encoding="utf-8")
        return caminho

    def test_troca_o_ano(self, tmp_path, monkeypatch):
        caminho = self.config(tmp_path, "X = 1\nANO_REFERENCIA_MALHA = 2022\nY = 2\n")
        monkeypatch.setattr(domingo, "CONFIG", caminho)
        domingo.trocar_ano_de_referencia(2026)
        assert caminho.read_text(encoding="utf-8") == "X = 1\nANO_REFERENCIA_MALHA = 2026\nY = 2\n"

    def test_e_idempotente(self, tmp_path, monkeypatch):
        caminho = self.config(tmp_path, "ANO_REFERENCIA_MALHA = 2026\n")
        monkeypatch.setattr(domingo, "CONFIG", caminho)
        domingo.trocar_ano_de_referencia(2026)
        assert caminho.read_text(encoding="utf-8") == "ANO_REFERENCIA_MALHA = 2026\n"

    def test_recusa_se_a_linha_nao_for_unica(self, tmp_path, monkeypatch):
        caminho = self.config(tmp_path,
                              "ANO_REFERENCIA_MALHA = 2022\nANO_REFERENCIA_MALHA = 2018\n")
        monkeypatch.setattr(domingo, "CONFIG", caminho)
        with pytest.raises(SystemExit):
            domingo.trocar_ano_de_referencia(2026)

    def test_recusa_se_nao_achar_a_linha(self, tmp_path, monkeypatch):
        caminho = self.config(tmp_path, "OUTRA_COISA = 2022\n")
        monkeypatch.setattr(domingo, "CONFIG", caminho)
        with pytest.raises(SystemExit):
            domingo.trocar_ano_de_referencia(2026)
        assert caminho.read_text(encoding="utf-8") == "OUTRA_COISA = 2022\n"


class TestConferirBase:
    """Se a mesclagem não aconteceu, o pipeline rodaria três horas e publicaria
    a eleição anterior como se fosse a nova."""

    def base(self, tmp_path, anos):
        caminho = tmp_path / "votos.parquet"
        pd.DataFrame([{"ano_eleicao": a, "qt_votos": v} for a, v in anos]).to_parquet(
            caminho, index=False)
        return caminho

    def test_passa_com_o_ano_presente(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(domingo, "BASE_DE_VOTOS", self.base(tmp_path, [(2022, 10), (2026, 7)]))
        domingo.conferir_base(2026)
        assert "2026: 7" in capsys.readouterr().out

    def test_para_se_o_ano_nao_entrou(self, tmp_path, monkeypatch):
        monkeypatch.setattr(domingo, "BASE_DE_VOTOS", self.base(tmp_path, [(2022, 10)]))
        with pytest.raises(SystemExit):
            domingo.conferir_base(2026)

    def test_para_se_a_base_nao_existe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(domingo, "BASE_DE_VOTOS", tmp_path / "nao_existe.parquet")
        with pytest.raises(SystemExit):
            domingo.conferir_base(2026)


class TestEtapas:
    def test_retomar_corta_a_lista_na_etapa_pedida(self):
        assert domingo.ETAPAS[domingo.ETAPAS.index("pipeline"):] == ["pipeline"]
        assert domingo.ETAPAS[domingo.ETAPAS.index("conferencia"):] == [
            "conferencia", "referencia", "pipeline"]

    def test_a_conferencia_vem_antes_de_mexer_em_qualquer_coisa(self):
        """A ordem é a trava: nada de estado muda antes das duas conferências."""
        assert domingo.ETAPAS.index("conferencia") < domingo.ETAPAS.index("referencia")
        assert domingo.ETAPAS.index("conferencia") < domingo.ETAPAS.index("pipeline")

    def test_confere_presidente_e_deputado_federal(self):
        assert domingo.CONFERENCIAS == [(1, "br"), (6, "uf")]


class TestRepeticaoDeRede:
    """Meio milhão de downloads leva horas; uma falha de rede não pode matar tudo.

    O caso real: a coleta caiu aos 83 minutos, com 143 mil boletins já baixados,
    porque o servidor do TSE cortou uma conexão. A repetição só capturava
    `URLError` e `TimeoutError`, e `RemoteDisconnected` não é nenhum dos dois —
    passava direto e derrubava a execução inteira.
    """

    @staticmethod
    def cliente_que_falha(erros):
        """Um cliente cujo urlopen levanta os erros da lista, e depois responde."""
        import urllib.request

        restantes = list(erros)
        chamadas = {"n": 0}

        class Resposta:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"ok"

        def falso(pedido, timeout=None):
            chamadas["n"] += 1
            if restantes:
                raise restantes.pop(0)
            return Resposta()

        c = tse.Cliente()
        c.limite = types.SimpleNamespace(aguardar=lambda: None)
        return c, falso, chamadas, urllib.request

    def test_tenta_de_novo_quando_a_conexao_cai(self, monkeypatch):
        erro = http.client.RemoteDisconnected("Remote end closed connection")
        cliente, falso, chamadas, req = self.cliente_que_falha([erro])
        monkeypatch.setattr(req, "urlopen", falso)
        monkeypatch.setattr(tse.time, "sleep", lambda s: None)
        assert cliente.baixar("qualquer/caminho") == b"ok"
        assert chamadas["n"] == 2          # falhou uma vez, acertou na seguinte

    def test_desiste_depois_do_limite_de_tentativas(self, monkeypatch):
        erros = [http.client.RemoteDisconnected("corte") for _ in range(tse.TENTATIVAS)]
        cliente, falso, chamadas, req = self.cliente_que_falha(erros)
        monkeypatch.setattr(req, "urlopen", falso)
        monkeypatch.setattr(tse.time, "sleep", lambda s: None)
        with pytest.raises(http.client.RemoteDisconnected):
            cliente.baixar("qualquer/caminho")
        assert chamadas["n"] == tse.TENTATIVAS

    def test_secao_que_falha_vira_pendencia_e_nao_derruba(self, monkeypatch):
        """A coleta anota e segue: um buraco pequeno, que a conferência mede,
        é melhor que nenhum resultado."""
        def explode(*a, **kw):
            raise http.client.RemoteDisconnected("corte")
        monkeypatch.setattr(coletar, "_baixar_secao", explode)
        secao = {"uf": "RR", "municipio": "03018", "zona": "0001", "secao": "0001"}
        _, dados, motivo, _ = coletar.baixar_secao(None, None, secao)
        assert dados is None
        assert "RemoteDisconnected" in motivo


class TestResumoOficialQueNaoBate:
    """O caso de Pernambuco, deputado federal, 2026.

    O arquivo do TSE traz um resumo (`vvc`) e as partes (`vnom`, `vl`, `van`,
    `vansj`). Em PE o resumo saiu 772 votos abaixo das próprias partes — o
    bastante para a identidade `tv = vvc+vb+tvn` também não fechar. A causa são
    candidaturas sub judice, contadas em `vansj` e ausentes da lista do
    resultado.

    Nosso número batia exatamente com as partes. Comparar contra o resumo
    acusaria erro nosso onde não havia, e barraria a publicação do país inteiro
    por uma soma de rodapé alheia.
    """

    @staticmethod
    def com_resumo_menor(diferenca):
        """Um oficial coerente, com o resumo rebaixado em `diferenca`."""
        conteudo = oficial(vnom=5_101_066, vl=163_437, vansj=1_124,
                           vb=387_863, vn=251_191, vnt=555)
        v = conteudo["v"]
        v["vvc"] = str(int(v["vvc"]) - diferenca)
        return conteudo

    def test_vale_o_detalhe_e_nao_o_resumo(self):
        totais, _, _, _ = conferir.totais_do_oficial(self.com_resumo_menor(772))
        # 5.101.066 + 163.437 + 1.124 (partes) + 555 (nulo técnico)
        assert totais["validos"] == 5_266_182

    def test_a_discordancia_vira_nota_e_nao_barra(self):
        _, _, avisos, notas = conferir.totais_do_oficial(self.com_resumo_menor(772))
        assert avisos == []
        assert any("não bate com as próprias partes" in n for n in notas)

    def test_arquivo_coerente_nao_gera_nota(self):
        _, _, avisos, notas = conferir.totais_do_oficial(self.com_resumo_menor(0))
        assert avisos == [] and notas == []

    def test_resumo_maior_que_as_partes_continua_valendo(self):
        """O máximo protege os dois lados: se o resumo for o maior, é ele."""
        totais, _, _, _ = conferir.totais_do_oficial(self.com_resumo_menor(-500))
        assert totais["validos"] == 5_266_182 + 500

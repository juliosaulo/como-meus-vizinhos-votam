"""Parser do Boletim de Urna.

O BU é o único lugar onde o resultado aparece **por seção** durante a apuração —
os arquivos agregados do TSE param na zona eleitoral, e a nossa pergunta é por
local de votação. Daí este módulo.

O arquivo é ASN.1 em DER, sem schema público junto. A estrutura abaixo foi
reconhecida a partir de boletins reais e está conferida contra os próprios
totais que o BU declara (ver `conferir`). O formato:

    envelope
      ├── cabeçalho (data/hora, pleito)
      ├── identificação da seção: município, zona, LOCAL, seção
      └── octet string ──► boletim
                             ├── identificação da seção (de novo)
                             ├── comparecimento
                             └── resultados por eleição
                                   └── por cargo
                                         └── votáveis: tipo, quantidade, partido/número

O campo que torna tudo isto possível é o **local** na identificação da seção: é
ele que liga a urna ao prédio, que é a unidade do projeto.
"""

from __future__ import annotations

from dataclasses import dataclass

import der

# Tipos de voto, do enumerado `TipoVoto` da especificação ASN.1 do TSE. Os nomes
# dos quatro primeiros são os do nosso pipeline (passo 21), para a conversão não
# precisar traduzir depois.
#
# O quinto saiu da especificação e não teria sido descoberto lendo arquivos: um
# cargo pode não ter candidato nenhum, e aí os votos daquele cargo vêm com este
# tipo. Sem reconhecê-lo, `conferir` acusaria tipo desconhecido e **a urna
# inteira seria descartada** — num município onde isso acontecesse, o local
# ficaria sem resultado sem ninguém entender por quê.
TIPOS_DE_VOTO = {
    1: "nominal",
    2: "branco",
    3: "nulo",
    4: "legenda",
    5: "cargo_sem_candidato",
}

# Fase em que o arquivo foi gerado (enumerado `Fase` da especificação). A urna
# gera boletim em simulado e em treinamento com a mesma estrutura do oficial —
# contar um deles seria publicar voto que não existiu.
FASES = {1: "simulado", 2: "oficial", 3: "treinamento"}

# Códigos de cargo, do enumerado `CargoConstitucional` da especificação:
# 1 presidente, 3 governador, 5 senador, 6 deputado federal, 7 deputado estadual,
# 8 deputado distrital, 11 prefeito, 13 vereador.
#
# O campo é um CHOICE: etiqueta [1] para cargo constitucional e [2] para consulta
# popular, cujos números vão de 25 a 99. Como só pedimos 1 e 6, não há colisão
# possível entre os dois espaços de numeração.
PRESIDENTE = 1
DEPUTADO_FEDERAL = 6


@dataclass(frozen=True)
class Voto:
    tipo: str          # nominal | legenda | branco | nulo
    quantidade: int
    partido: int | None
    numero: int | None  # número digitado na urna


@dataclass
class Cargo:
    codigo: int
    comparecimento: int
    votos: list[Voto]

    @property
    def total(self) -> int:
        return sum(v.quantidade for v in self.votos)


@dataclass
class Boletim:
    municipio: str      # código do TSE, 5 dígitos
    zona: int
    local: int
    secao: int
    pleito: int
    fase: str           # oficial | simulado | treinamento
    eleicoes: dict[int, list[Cargo]]   # código da eleição → cargos

    @property
    def id_local_votacao(self) -> str:
        """A chave do pipeline, montada como no passo 21: UF entra depois."""
        return f"{self.municipio}_{self.zona}_{self.local}"


class BoletimInvalido(Exception):
    """O arquivo não tem a estrutura de BU que este módulo sabe ler.

    Existe para que uma mudança de formato apareça como recusa contada, e não
    como exceção no meio de meio milhão de arquivos. Quem chama decide o que
    fazer com a recusa; o que não pode acontecer é ler o campo errado e seguir
    como se nada fosse.
    """


def _inteiro_de_contexto(no: der.No) -> int:
    """Campos com etiqueta de contexto guardam o inteiro cru, sem tipo universal."""
    _exigir(not no.construido, f"esperava um inteiro cru em [ctx{no.numero}], veio um nó com filhos")
    _exigir(len(no.conteudo) <= 8, f"inteiro de contexto com {len(no.conteudo)} bytes")
    return int.from_bytes(no.conteudo, "big")


def _exigir(condicao: bool, mensagem: str) -> None:
    if not condicao:
        raise BoletimInvalido(mensagem)


def _filho(no: der.No, posicao: int, nome: str) -> der.No:
    """Acessa um campo pela posição, dizendo qual campo faltou quando falta."""
    _exigir(no.construido, f"{nome}: esperava um nó com filhos")
    _exigir(len(no.filhos) > posicao,
            f"{nome}: esperava ao menos {posicao + 1} campo(s), veio {len(no.filhos)}")
    return no.filhos[posicao]


# ---------------------------------------------------------------- forma
#
# O formato do BU **muda entre eleições**. Comparando a especificação ASN.1 que o
# TSE publicou em 2022 com um boletim real de 2024:
#
#   ResultadoVotacaoPorEleicao   2022: 3 campos   2024: 7 campos
#   Carga.dataHoraCarga          2022: texto      2024: outra estrutura
#
# O leitor oficial do TSE, com a especificação de 2022, **não lê** um boletim de
# 2024 — testado. Ou seja, ler por posição fixa é apostar que 2026 não mexeu em
# nada; e ler só pela etiqueta também não salva, porque as etiquetas mudaram.
#
# Então: tenta a posição conhecida e, se o que vier não tiver a forma esperada,
# procura entre os outros campos aquele que tem. O que não acontece nunca é
# aceitar um campo que não parece o que deveria ser — aí é recusa.


def _eh_votavel(no: der.No) -> bool:
    """`TotalVotosVotavel`: tipo e quantidade em etiquetas de contexto."""
    return (no.construido and len(no.filhos) >= 2
            and not no.filhos[0].construido and no.filhos[0].classe == der.CLASSE_CONTEXTO
            and not no.filhos[1].construido and no.filhos[1].classe == der.CLASSE_CONTEXTO)


def _eh_lista_de_cargos(no: der.No) -> bool:
    """`SEQUENCE OF TotalVotosCargo`: cada um termina numa lista de votáveis."""
    return (no.construido and bool(no.filhos)
            and all(c.construido and len(c.filhos) >= 3
                    and c.filhos[2].construido and bool(c.filhos[2].filhos)
                    and all(_eh_votavel(v) for v in c.filhos[2].filhos)
                    for c in no.filhos))


def _eh_lista_de_grupos(no: der.No) -> bool:
    """`SEQUENCE OF ResultadoVotacao`: comparecimento mais os cargos."""
    return (no.construido and bool(no.filhos)
            and all(g.construido and len(g.filhos) >= 3 and _eh_lista_de_cargos(g.filhos[2])
                    for g in no.filhos))


def _eh_lista_de_eleicoes(no: der.No) -> bool:
    return (no.construido and bool(no.filhos)
            and all(e.construido and any(_eh_lista_de_grupos(f) for f in e.filhos)
                    for e in no.filhos))


def _campo_por_forma(no: der.No, posicao: int, forma, nome: str) -> der.No:
    """O campo na posição conhecida; se não tiver a forma certa, o que tiver."""
    _exigir(no.construido, f"{nome}: esperava um nó com filhos")
    if len(no.filhos) > posicao and forma(no.filhos[posicao]):
        return no.filhos[posicao]
    for candidato in no.filhos:
        if forma(candidato):
            return candidato
    raise BoletimInvalido(
        f"{nome}: nenhum dos {len(no.filhos)} campos tem a forma esperada")


def ler(dados: bytes) -> Boletim:
    """Lê os bytes de um arquivo `-bu.dat`.

    Levanta `BoletimInvalido` para qualquer surpresa de estrutura — nunca
    devolve um boletim montado a partir de campos que não conferem.
    """
    try:
        return _ler(dados)
    except BoletimInvalido:
        raise
    except (IndexError, ValueError, AttributeError, TypeError) as erro:
        raise BoletimInvalido(f"estrutura inesperada: {type(erro).__name__}: {erro}") from erro


def _ler(dados: bytes) -> Boletim:
    raiz = der.ler(dados)
    _exigir(bool(raiz), "arquivo vazio")
    envelope = raiz[0]
    _exigir(envelope.construido and bool(envelope.filhos), "não parece um BU: envelope sem campos")
    interno_bruto = envelope.filhos[-1]
    _exigir(not interno_bruto.construido and bool(interno_bruto.conteudo),
            "não parece um BU: o último campo do envelope não é o boletim")
    interno_nos = der.ler(interno_bruto.conteudo)
    _exigir(bool(interno_nos), "não parece um BU: boletim vazio")
    interno = interno_nos[0]

    pleito = _inteiro_de_contexto(_filho(_filho(interno, 0, "cabeçalho"), 1, "pleito"))

    no_fase = _filho(interno, 1, "fase")
    fase = FASES.get(no_fase.inteiro if not no_fase.construido else -1, "desconhecida")

    identificacao = _filho(interno, 3, "identificação da seção")
    municipio_zona = _filho(identificacao, 0, "município e zona")
    municipio_numero = _filho(municipio_zona, 0, "município").inteiro
    zona = _filho(municipio_zona, 1, "zona").inteiro
    local = _filho(identificacao, 1, "local").inteiro
    secao = _filho(identificacao, 2, "seção").inteiro
    for nome, valor in (("município", municipio_numero), ("zona", zona),
                        ("local", local), ("seção", secao)):
        _exigir(valor >= 0, f"{nome} negativo: {valor}")
    _exigir(municipio_numero <= 99999, f"código de município fora da faixa: {municipio_numero}")

    resultados = _campo_por_forma(interno, 8, _eh_lista_de_eleicoes,
                                  "resultados por eleição")

    eleicoes: dict[int, list[Cargo]] = {}
    for eleicao in resultados.filhos:
        codigo_eleicao = _filho(eleicao, 0, "código da eleição").inteiro
        grupos = _campo_por_forma(eleicao, 4, _eh_lista_de_grupos,
                                  f"grupos da eleição {codigo_eleicao}")
        cargos: list[Cargo] = []
        for grupo in grupos.filhos:
            comparecimento = _filho(grupo, 1, "comparecimento").inteiro
            _exigir(comparecimento >= 0, f"comparecimento negativo: {comparecimento}")
            for totais in _filho(grupo, 2, "totais por cargo").filhos:
                votaveis = _filho(totais, 2, "votáveis")
                _exigir(bool(votaveis.filhos), "cargo sem nenhum votável")
                cargos.append(Cargo(
                    codigo=_inteiro_de_contexto(_filho(totais, 0, "código do cargo")),
                    comparecimento=comparecimento,
                    votos=[_ler_voto(v) for v in votaveis.filhos],
                ))
        _exigir(bool(cargos), f"eleição {codigo_eleicao} sem nenhum cargo")
        eleicoes[codigo_eleicao] = cargos

    return Boletim(municipio=f"{municipio_numero:05d}", zona=zona, local=local, secao=secao,
                   pleito=pleito, fase=fase, eleicoes=eleicoes)


def _ler_voto(no: der.No) -> Voto:
    codigo = _inteiro_de_contexto(_filho(no, 0, "tipo do voto"))
    quantidade = _filho(no, 1, "quantidade de votos").inteiro
    _exigir(quantidade >= 0, f"quantidade de votos negativa: {quantidade}")
    identificacao = _filho(no, 2, "identificação do votável")
    partido = numero = None
    # Só voto em alguém tem identificação com partido e número; branco e nulo
    # trazem outra coisa nessa posição (o ordinal do votável).
    if identificacao.construido and len(identificacao.filhos) == 2:
        partido = identificacao.filhos[0].inteiro
        numero = identificacao.filhos[1].inteiro
    return Voto(
        tipo=TIPOS_DE_VOTO.get(codigo, f"desconhecido_{codigo}"),
        quantidade=quantidade, partido=partido, numero=numero,
    )


# Quantos votos cada eleitor dá em cada cargo. É um em quase todos — mas o
# Senado renova um terço ou dois terços das cadeiras a cada quatro anos, e na
# renovação de dois terços cada eleitor escolhe dois senadores. Aí a soma do
# cargo dá o dobro do comparecimento, sem nada de errado. Foi o que aconteceu em
# 2026: a conferência reprovava todo boletim do país por causa disto.
VOTOS_POR_ELEITOR = {5: (1, 2)}   # 5 = senador
PADRAO_VOTOS_POR_ELEITOR = (1,)


def conferir(boletim: Boletim) -> list[str]:
    """Problemas que um BU bem formado não pode ter.

    A conferência que importa: **a soma dos votos de um cargo é o comparecimento**
    daquela urna, vezes o número de votos que cada eleitor dá naquele cargo —
    um, salvo no Senado em renovação de dois terços. Se isso não fecha, o parser
    leu errado, e é melhor saber aqui do que descobrir depois de publicar.
    """
    problemas = []
    for codigo_eleicao, cargos in boletim.eleicoes.items():
        for cargo in cargos:
            por_eleitor = VOTOS_POR_ELEITOR.get(cargo.codigo, PADRAO_VOTOS_POR_ELEITOR)
            aceitos = {cargo.comparecimento * n for n in por_eleitor}
            if cargo.total not in aceitos:
                esperado = " ou ".join(f"{v:,}" for v in sorted(aceitos))
                problemas.append(
                    f"eleição {codigo_eleicao}, cargo {cargo.codigo}: votos somam "
                    f"{cargo.total:,} e o comparecimento declarado é "
                    f"{cargo.comparecimento:,} (esperava {esperado})"
                )
            for voto in cargo.votos:
                if voto.quantidade < 0:
                    problemas.append(f"cargo {cargo.codigo}: quantidade negativa")
                if voto.tipo.startswith("desconhecido"):
                    problemas.append(f"cargo {cargo.codigo}: tipo de voto {voto.tipo}")
                if voto.tipo == "nominal" and voto.numero is None:
                    problemas.append(f"cargo {cargo.codigo}: voto nominal sem número")
    return problemas

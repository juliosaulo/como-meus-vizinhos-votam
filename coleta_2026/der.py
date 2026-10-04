"""Leitor mínimo de DER (a codificação binária em que o BU é publicado).

O Boletim de Urna é um arquivo ASN.1 em DER. Aqui não há schema: este módulo só
sabe quebrar os bytes em nós — etiqueta, tamanho e conteúdo —, o que basta para
percorrer a árvore e para `bu.py` identificar os campos pela posição.

DER é simples o bastante para caber em 80 linhas:

    [etiqueta] [tamanho] [conteúdo]

A etiqueta diz a classe (universal, contexto…), se o nó é construído (contém
outros nós) e o número do tipo. O tamanho é curto (até 127, num byte) ou longo
(o primeiro byte diz quantos bytes de tamanho vêm depois).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CLASSE_UNIVERSAL = 0
CLASSE_CONTEXTO = 2

# Os tipos universais que aparecem no BU.
INTEIRO = 2
BYTES = 4
ENUMERADO = 10
TEXTO_IMPRIMIVEL = 19
TEXTO_IA5 = 22
HORA_GENERALIZADA = 24
SEQUENCIA = 16
CONJUNTO = 17


@dataclass
class No:
    classe: int
    construido: bool
    numero: int
    conteudo: bytes
    filhos: list["No"] = field(default_factory=list)
    inicio: int = 0

    @property
    def inteiro(self) -> int:
        """O conteúdo como inteiro com sinal, que é como o DER guarda."""
        return int.from_bytes(self.conteudo, "big", signed=True)

    @property
    def texto(self) -> str:
        return self.conteudo.decode("latin-1", errors="replace")

    def __repr__(self) -> str:
        tipo = "ctx" if self.classe == CLASSE_CONTEXTO else "uni"
        corpo = f"{len(self.filhos)} filho(s)" if self.construido else repr(self.conteudo[:24])
        return f"<{tipo}{self.numero} {corpo}>"


def ler(dados: bytes, posicao: int = 0, fim: int | None = None) -> list[No]:
    """Lê uma sequência de nós DER no intervalo dado."""
    fim = len(dados) if fim is None else fim
    nos: list[No] = []
    while posicao < fim:
        inicio = posicao
        # Fatiar em Python é permissivo: `dados[a:b]` com b além do fim devolve o
        # que houver, sem reclamar. Num arquivo truncado — download interrompido,
        # por exemplo — isso daria um boletim lido pela metade e com cara de
        # íntegro. Daí as conferências explícitas de limite.
        if posicao + 2 > fim:
            raise ValueError("DER truncado: falta etiqueta ou tamanho")
        etiqueta = dados[posicao]
        posicao += 1
        classe = etiqueta >> 6
        construido = bool(etiqueta & 0x20)
        numero = etiqueta & 0x1F
        if numero == 0x1F:  # etiqueta longa, pouco provável aqui, mas barata de tratar
            numero = 0
            while True:
                if posicao >= fim:
                    raise ValueError("DER truncado: etiqueta longa sem fim")
                byte = dados[posicao]
                posicao += 1
                numero = (numero << 7) | (byte & 0x7F)
                if not byte & 0x80:
                    break

        if posicao >= fim:
            raise ValueError("DER truncado: falta o tamanho")
        primeiro = dados[posicao]
        posicao += 1
        if primeiro & 0x80:
            n = primeiro & 0x7F
            if n > 4 or posicao + n > fim:
                raise ValueError("DER com tamanho inválido")
            tamanho = int.from_bytes(dados[posicao:posicao + n], "big")
            posicao += n
        else:
            tamanho = primeiro

        if posicao + tamanho > fim:
            raise ValueError("DER truncado: conteúdo menor que o anunciado")
        conteudo = dados[posicao:posicao + tamanho]
        no = No(classe=classe, construido=construido, numero=numero, conteudo=conteudo,
                inicio=inicio)
        if construido:
            no.filhos = ler(dados, posicao, posicao + tamanho)
        nos.append(no)
        posicao += tamanho
    return nos


def desenhar(nos: list[No], nivel: int = 0, limite_profundidade: int = 12) -> str:
    """Árvore em texto, para explorar um arquivo novo."""
    if nivel > limite_profundidade:
        return ""
    linhas = []
    for no in nos:
        rotulo = f"{'  ' * nivel}{'ctx' if no.classe == CLASSE_CONTEXTO else 'uni'}{no.numero}"
        if no.construido:
            linhas.append(f"{rotulo}  ({len(no.filhos)})")
            linhas.append(desenhar(no.filhos, nivel + 1, limite_profundidade))
        else:
            valor = no.inteiro if no.numero in (INTEIRO, ENUMERADO) and len(no.conteudo) <= 8 \
                else no.conteudo[:28]
            linhas.append(f"{rotulo}  = {valor!r}")
    return "\n".join(l for l in linhas if l)

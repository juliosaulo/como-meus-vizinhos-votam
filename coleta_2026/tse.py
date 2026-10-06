"""Acesso ao servidor de divulgação de resultados do TSE.

Este módulo não sabe nada de eleição: ele sabe descobrir onde os arquivos estão
e buscá-los sem levar bloqueio. Todo o resto do projeto fala com o TSE por aqui.

Duas regras do TSE moldam o código inteiro (apresentação da audiência técnica de
julho/2026, "Regras de consumo dos arquivos"):

- **100 requisições por IP por segundo**, com bloqueio de 10 minutos, renovado
  enquanto o excesso continuar;
- **pedir endereço errado (404) também pode bloquear** — então nenhuma URL é
  inventada aqui. Todas saem dos arquivos de configuração, como o guia manda.

Por isso o limitador de taxa é obrigatório e fica embutido no cliente, em vez de
ser responsabilidade de quem chama.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass

BASE = "https://resultados.tse.jus.br"
AMBIENTE_OFICIAL = "oficial"

# 60% do teto do TSE. Sobra folga para a variação do relógio e para qualquer
# outra coisa que esteja usando a mesma saída de internet.
REQUISICOES_POR_SEGUNDO = 60

# Depois de um erro, espera crescente: 1s, 2s, 4s… Um bloqueio dura 10 minutos,
# então vale esperar bastante antes de desistir.
TENTATIVAS = 5
ESPERA_INICIAL_S = 1.0

UA = "como-meus-vizinhos-votam/1.0 (coleta de resultados; contato no site)"


class LimiteDeTaxa:
    """Janela deslizante de um segundo, compartilhada entre threads."""

    def __init__(self, por_segundo: int = REQUISICOES_POR_SEGUNDO):
        self.por_segundo = por_segundo
        self._marcas: deque[float] = deque()
        self._trava = threading.Lock()

    def aguardar(self) -> None:
        while True:
            with self._trava:
                agora = time.monotonic()
                while self._marcas and agora - self._marcas[0] >= 1.0:
                    self._marcas.popleft()
                if len(self._marcas) < self.por_segundo:
                    self._marcas.append(agora)
                    return
                espera = 1.0 - (agora - self._marcas[0])
            time.sleep(max(espera, 0.005))


@dataclass
class Eleicao:
    """Uma eleição dentro de um pleito, como o EA11 a descreve."""

    codigo: str          # ex.: "6257"
    nome: str
    turno: str
    ciclo: str           # ex.: "ele2026"
    pleito: str          # ex.: "3220"
    data: str            # dd/mm/aaaa

    @property
    def sufixo_eleicao(self) -> str:
        """`e<ELEICA>`: seis dígitos, com zeros à esquerda."""
        return f"e{int(self.codigo):06d}"

    @property
    def sufixo_pleito(self) -> str:
        return f"p{int(self.pleito):06d}"


class Cliente:
    """Busca arquivos do TSE respeitando o limite de requisições."""

    def __init__(self, ambiente: str = AMBIENTE_OFICIAL, base: str = BASE,
                 limite: LimiteDeTaxa | None = None):
        self.ambiente = ambiente
        self.base = base.rstrip("/")
        self.limite = limite or LimiteDeTaxa()

    # ---------------------------------------------------------------- rede
    def baixar(self, caminho: str, opcional: bool = False) -> bytes | None:
        """Baixa um caminho relativo à raiz do servidor.

        `opcional=True` devolve None em 404 em vez de levantar — usado só onde a
        ausência é esperada (seção sem arquivo de urna ainda).
        """
        url = f"{self.base}/{caminho.lstrip('/')}"
        espera = ESPERA_INICIAL_S
        for tentativa in range(1, TENTATIVAS + 1):
            self.limite.aguardar()
            try:
                pedido = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(pedido, timeout=30) as resposta:
                    return resposta.read()
            except urllib.error.HTTPError as erro:
                if erro.code == 404 and opcional:
                    return None
                # 403 e 429 são a cara do bloqueio: esperar é a única saída.
                if erro.code in (403, 429, 500, 502, 503) and tentativa < TENTATIVAS:
                    time.sleep(espera)
                    espera *= 2
                    continue
                raise
            # Conexão cortada no meio é falha de rede como qualquer outra, e
            # tem de ser tentada de novo. `RemoteDisconnected` não é `URLError`,
            # então escapava desta rede e derrubava a coleta inteira: numa
            # execução de cinco horas, uma desconexão do servidor matou 83
            # minutos de trabalho. As duas famílias cobrem o caso — ele é ao
            # mesmo tempo `ConnectionResetError` e `HTTPException`.
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    http.client.HTTPException):
                if tentativa == TENTATIVAS:
                    raise
                time.sleep(espera)
                espera *= 2
        return None

    def json(self, caminho: str, opcional: bool = False) -> dict | None:
        bruto = self.baixar(caminho, opcional=opcional)
        return None if bruto is None else json.loads(bruto)

    # ---------------------------------------------------------------- descoberta
    def eleicoes(self) -> list[Eleicao]:
        """Lê o EA11 e devolve as eleições disponíveis, da mais nova para a mais antiga."""
        config = self.json(f"{self.ambiente}/comum/config/ele-c.json")
        achadas: list[Eleicao] = []
        for pleito in config["pl"]:
            for eleicao in pleito.get("e", []):
                achadas.append(Eleicao(
                    codigo=str(eleicao["cd"]), nome=eleicao["nm"], turno=str(eleicao["t"]),
                    ciclo=pleito["c"], pleito=str(pleito["cd"]), data=pleito["dt"],
                ))
        return achadas

    def eleicao_federal(self, ano: int, turno: int = 1) -> Eleicao:
        """A eleição ordinária federal do ano — a que tem presidente e deputado federal.

        O código muda a cada eleição e não pode ser fixado no código: ele sai do
        arquivo de configuração, que é a fonte que o TSE manda usar.
        """
        candidatas = [
            e for e in self.eleicoes()
            if e.ciclo == f"ele{ano}" and e.turno == str(turno)
            and "federal" in e.nome.lower() and "ordin" in e.nome.lower()
        ]
        if not candidatas:
            raise LookupError(
                f"não achei a eleição ordinária federal de {ano}, {turno}º turno, no ele-c.json"
            )
        if len(candidatas) > 1:
            nomes = ", ".join(f"{c.codigo} ({c.nome})" for c in candidatas)
            raise LookupError(f"mais de uma eleição federal candidata em {ano}: {nomes}")
        return candidatas[0]

    # ---------------------------------------------------------------- caminhos
    def caminho_config_secoes(self, eleicao: Eleicao, uf: str) -> str:
        """EA16 — todas as seções de uma UF naquele pleito."""
        return (f"{self.ambiente}/{eleicao.ciclo}/arquivo-urna/{eleicao.pleito}"
                f"/config/{uf.lower()}/{uf.lower()}-{eleicao.sufixo_pleito}-cs.json")

    def caminho_aux_secao(self, eleicao: Eleicao, uf: str, municipio: str, zona: str,
                          secao: str) -> str:
        """EA18 — os hashes dos arquivos de urna de uma seção."""
        pasta = (f"{self.ambiente}/{eleicao.ciclo}/arquivo-urna/{eleicao.pleito}"
                 f"/dados/{uf.lower()}/{municipio}/{zona}/{secao}")
        nome = (f"{eleicao.sufixo_pleito}-{uf.lower()}-m{municipio}-z{zona}-s{secao}-aux.json")
        return f"{pasta}/{nome}"

    def caminho_arquivo_urna(self, eleicao: Eleicao, uf: str, municipio: str, zona: str,
                             secao: str, hash_: str, nome: str) -> str:
        return (f"{self.ambiente}/{eleicao.ciclo}/arquivo-urna/{eleicao.pleito}"
                f"/dados/{uf.lower()}/{municipio}/{zona}/{secao}/{hash_}/{nome}")

    def caminho_resultado_br(self, eleicao: Eleicao, cargo: str = "0001") -> str:
        """EA20 — o resultado oficial agregado, usado para conferir o nosso."""
        return (f"{self.ambiente}/{eleicao.ciclo}/{eleicao.codigo}/dados/br"
                f"/br-c{cargo}-{eleicao.sufixo_eleicao}-u.json")


# Em qual espécie de eleição cada cargo é apurado. Presidente sai na federal;
# governador, senador e os dois deputados saem na estadual, porque a unidade
# eleitoral deles é a UF; prefeito e vereador, na municipal. O endereço de todo
# resultado agregado leva o código da eleição, então errar isto é pedir o
# arquivo de um cargo no lugar de outro — e receber 404.
ESPECIE_DO_CARGO = {1: "federal", 3: "estadual", 5: "estadual", 6: "estadual",
                    7: "estadual", 11: "municipal", 13: "municipal"}


def eleicoes_por_cargo(cliente: "Cliente", ano: int, turno: int,
                       cargos: list[int], federal: Eleicao) -> dict[str, str]:
    """Código da eleição de cada cargo pedido, descoberto no ele-c.json.

    A federal já vem resolvida por quem chamou; as outras saem da mesma lista,
    pelo nome, como `eleicao_federal` faz. Cargo cuja eleição não aparece fica
    de fora: quem chama decide o que fazer, e isso é melhor do que apontar para
    a errada em silêncio.
    """
    por_especie = {"federal": federal.codigo}
    for e in cliente.eleicoes():
        nome = e.nome.lower()
        if e.ciclo != f"ele{ano}" or e.turno != str(turno) or "ordin" not in nome:
            continue
        for especie in ("estadual", "municipal"):
            if especie in nome:
                por_especie.setdefault(especie, e.codigo)
    return {str(c): por_especie[ESPECIE_DO_CARGO[c]] for c in cargos
            if ESPECIE_DO_CARGO.get(c) in por_especie}


def secoes_da_uf(config: dict, so_com_aux: bool = False) -> list[dict]:
    """Achata o EA16 numa lista de seções.

    Só as seções **principais** entram: as agregadas não têm urna própria, e os
    eleitores delas votam na principal (o próprio EA16 marca isso em `nsp`).
    Pedir arquivo de urna de uma agregada daria 404 — e 404 arrisca bloqueio.

    Com `so_com_aux`, ficam apenas as seções cujo arquivo auxiliar já existe.
    Quem diz quais são é o próprio TSE: segundo o EA16, `da` e `ha` estão
    "disponível somente para a seção principal e **após a geração do arquivo
    auxiliar correspondente**". É isto que permite coletar durante a apuração
    sem pedir nada que ainda não foi transmitido.
    """
    saida = []
    for abrangencia in config.get("abr", []):
        uf = abrangencia["cd"]
        for municipio in abrangencia.get("mu", []):
            for zona in municipio.get("zon", []):
                for secao in zona.get("sec", []):
                    if secao.get("nsp"):
                        continue
                    if so_com_aux and not secao.get("da"):
                        continue
                    saida.append({
                        "uf": uf,
                        "municipio": municipio["cd"],
                        "municipio_nome": municipio["nm"],
                        "zona": zona["cd"],
                        "secao": secao["ns"],
                        "data_aux": secao.get("da"),
                        "hora_aux": secao.get("ha"),
                    })
    return saida

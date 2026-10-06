"""Coletor dos Boletins de Urna.

Percorre as seções de cada UF e guarda o BU de cada urna. É a parte longa do
processo — 464 mil seções, duas requisições por seção — e por isso foi feita
para ser interrompida e retomada sem perder nada.

    python coleta_2026/coletar.py                      # a eleição federal de 2026
    python coleta_2026/coletar.py --uf RO SP           # só algumas UFs
    python coleta_2026/coletar.py --passadas 8 --espera 20
                                       # oito passadas, 20 min de espera entre elas
    python coleta_2026/coletar.py --ano 2024 --ciclo ele2024 --pleito 452 --eleicao 619
                                                       # ensaio com dado real já publicado

Quatro decisões que valem explicação:

- **Um zip por UF, não 464 mil arquivos soltos.** O sistema de arquivos sofre com
  centenas de milhares de arquivos de 5 KB, e o zip ainda serve de registro do
  que já foi baixado: retomar é ler a lista de nomes de dentro dele.
- **O BU cru é guardado, não o resultado já interpretado.** Se o parser precisar
  de conserto no meio da noite, conserta-se e reprocessa — sem baixar de novo.
- **Só o arquivo `bu`**, dos quatro que cada urna publica. O RDV e o log não
  entram na conta do projeto, e cada arquivo a mais seria mais 464 mil
  requisições.
- **Nada é pedido antes de existir.** A cada passada o arquivo de configuração é
  lido de novo, e dele saem só as seções que já têm arquivo auxiliar gerado —
  o EA16 marca isso em `da`/`ha`. Seção que ainda não transmitiu não é pedida,
  então a coleta pode começar antes do fim da apuração sem produzir 404, que é
  o que arrisca bloqueio de 10 minutos.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tse

PASTA_PADRAO = Path(__file__).resolve().parents[1] / "dados" / "bruto" / "bu"

# Quantas requisições em voo ao mesmo tempo. O teto de verdade é o limitador de
# taxa do cliente; isto só define quantas esperas cabem em paralelo.
LINHAS = 24


def nome_no_zip(secao: dict) -> str:
    return f"{secao['municipio']}/{secao['zona']}/{secao['secao']}.bu"


def escolher_hash(aux: dict) -> dict | None:
    """Qual transmissão vale, quando a seção transmitiu mais de uma vez.

    O EA18 dá situação a cada hash — Recebido, Rejeitado, Excluído, Totalizado —
    e diz que o auxiliar "poderá ser atualizado após cada totalização final ou
    retransmissão de arquivos de urna". Então o que vale é o hash **Totalizado**,
    que é o que entrou no resultado oficial; pegar o último da lista traria o
    arquivo rejeitado de uma seção que retransmitiu.

    Antes da totalização nenhum hash está totalizado ainda: aí vale o último
    recebido, que é o que o TSE também usa para montar o auxiliar.
    """
    hashes = aux.get("hashes") or []
    totalizados = [h for h in hashes if (h.get("st") or "").lower().startswith("totaliz")]
    candidatos = totalizados or hashes
    return candidatos[-1] if candidatos else None


def baixar_secao(cliente: tse.Cliente, eleicao: tse.Eleicao,
                 secao: dict) -> tuple[dict, bytes | None, str, str]:
    """Baixa o BU de uma seção, e nunca derruba a coleta por causa de uma.

    O cliente já tenta de novo em falha de rede. O que sobrevive a cinco
    tentativas vira pendência anotada, como a seção sem arquivo: meio milhão de
    downloads leva horas, e perder tudo por uma seção seria trocar um buraco
    pequeno, que a conferência mede, por nenhum resultado.
    """
    try:
        return _baixar_secao(cliente, eleicao, secao)
    except Exception as erro:                      # noqa: BLE001 — ver docstring
        return secao, None, f"falhou: {type(erro).__name__}: {erro}", ""


def _baixar_secao(cliente: tse.Cliente, eleicao: tse.Eleicao,
                  secao: dict) -> tuple[dict, bytes | None, str, str]:
    """Primeiro o auxiliar, que diz onde o BU está; depois o BU."""
    caminho_aux = cliente.caminho_aux_secao(
        eleicao, secao["uf"], secao["municipio"], secao["zona"], secao["secao"])
    aux = cliente.json(caminho_aux, opcional=True)
    if aux is None:
        return secao, None, "sem arquivo auxiliar", ""
    situacao = aux.get("st") or ""
    escolhido = escolher_hash(aux)
    if escolhido is None:
        return secao, None, f"sem hash (situação: {situacao})", situacao

    nome = next((a["nm"] for a in escolhido.get("arq", []) if a["tp"] == "bu"), None)
    if nome is None:
        return secao, None, "hash sem arquivo de BU", situacao

    dados = cliente.baixar(cliente.caminho_arquivo_urna(
        eleicao, secao["uf"], secao["municipio"], secao["zona"], secao["secao"],
        escolhido["hash"], nome), opcional=True)
    if dados is None:
        return secao, None, "BU anunciado mas ausente", situacao
    return secao, dados, "", situacao


def coletar_uf(cliente: tse.Cliente, eleicao: tse.Eleicao, uf: str, pasta: Path,
               ignorar_aux: bool = False) -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{uf.upper()}.zip"
    pendencias = pasta / f"{uf.upper()}-pendentes.json"

    config = cliente.json(cliente.caminho_config_secoes(eleicao, uf))
    principais = tse.secoes_da_uf(config)
    secoes = principais if ignorar_aux else tse.secoes_da_uf(config, so_com_aux=True)

    ja_tem: set[str] = set()
    if destino.exists():
        with zipfile.ZipFile(destino) as z:
            ja_tem = set(z.namelist())
    faltam = [s for s in secoes if nome_no_zip(s) not in ja_tem]

    numeros = {"uf": uf.upper(), "principais": len(principais), "publicadas": len(secoes),
               "baixadas": len(ja_tem), "sem_bu": 0, "nao_totalizadas": 0}
    print(f"  {uf.upper()}: {len(principais):,} seções | {len(secoes):,} com urna publicada | "
          f"{len(ja_tem):,} já baixadas | {len(faltam):,} a baixar"
          + (f" | {len(principais) - len(secoes):,} ainda sem urna"
             if len(secoes) < len(principais) else ""))
    if not faltam:
        return numeros

    trava = threading.Lock()
    inicio = time.monotonic()
    sem_bu: list[dict] = []
    situacoes: Counter = Counter()
    gravadas = 0

    with zipfile.ZipFile(destino, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        with ThreadPoolExecutor(max_workers=LINHAS) as piscina:
            for secao, dados, motivo, situacao in piscina.map(
                lambda s: baixar_secao(cliente, eleicao, s), faltam
            ):
                situacoes[situacao or "(sem auxiliar)"] += 1
                if dados is None:
                    sem_bu.append({**secao, "motivo": motivo})
                    continue
                with trava:
                    z.writestr(nome_no_zip(secao), dados)
                    gravadas += 1
                    if gravadas % 2000 == 0:
                        decorrido = time.monotonic() - inicio
                        print(f"    {gravadas:,}/{len(faltam):,} "
                              f"({gravadas / decorrido:.0f} BU/s)")

    if sem_bu:
        pendencias.write_text(json.dumps(sem_bu, ensure_ascii=False, indent=1), encoding="utf-8")
    elif pendencias.exists():
        pendencias.unlink()   # a passada anterior tinha pendência e esta resolveu
    decorrido = time.monotonic() - inicio
    print(f"    {uf.upper()}: {gravadas:,} BUs em {decorrido / 60:.1f} min "
          f"({gravadas / max(decorrido, 1):.0f}/s) | sem BU: {len(sem_bu):,}")
    # A situação da seção diz se o BU que pegamos é o definitivo: seção ainda não
    # totalizada pode retransmitir, e aí o hash que vale muda.
    nao_totalizadas = sum(q for s, q in situacoes.items() if not s.startswith("Totaliz"))
    if nao_totalizadas:
        print(f"    situação das seções: "
              + ", ".join(f"{s}: {q:,}" for s, q in situacoes.most_common()))
    return {**numeros, "baixadas": len(ja_tem) + gravadas, "sem_bu": len(sem_bu),
            "nao_totalizadas": nao_totalizadas}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ano", type=int, default=2026)
    parser.add_argument("--turno", type=int, default=1)
    parser.add_argument("--uf", nargs="+", help="só estas UFs")
    parser.add_argument("--pasta", type=Path, default=PASTA_PADRAO)
    parser.add_argument("--ciclo", help="ensaio: ciclo explícito, ex. ele2024")
    parser.add_argument("--pleito", help="ensaio: pleito explícito, ex. 452")
    parser.add_argument("--eleicao", help="ensaio: código da eleição, ex. 619")
    parser.add_argument("--taxa", type=int, default=tse.REQUISICOES_POR_SEGUNDO,
                        help="requisições por segundo (teto do TSE: 100)")
    parser.add_argument("--passadas", type=int, default=1,
                        help="quantas vezes repetir, para pegar o que ainda não "
                             "tinha urna publicada na passada anterior")
    parser.add_argument("--espera", type=int, default=20,
                        help="minutos de espera entre passadas")
    parser.add_argument("--ignorar-aux", action="store_true",
                        help="pede todas as seções principais, mesmo sem arquivo auxiliar "
                             "gerado (produz 404, que arrisca bloqueio — só para depuração)")
    args = parser.parse_args()

    cliente = tse.Cliente(limite=tse.LimiteDeTaxa(args.taxa))
    if args.eleicao:
        eleicao = tse.Eleicao(codigo=args.eleicao, nome="(informada na linha de comando)",
                              turno=str(args.turno), ciclo=args.ciclo, pleito=args.pleito,
                              data="")
    else:
        eleicao = cliente.eleicao_federal(args.ano, args.turno)

    print(f"=== coleta de BUs — {eleicao.nome}")
    print(f"    eleição {eleicao.codigo} | pleito {eleicao.pleito} | ciclo {eleicao.ciclo}")
    print(f"    taxa: {args.taxa} req/s | destino: {args.pasta}")
    print(f"    passadas: {args.passadas} | espera entre elas: {args.espera} min")

    ufs = [u.lower() for u in (args.uf or UFS)]
    inicio = time.monotonic()

    for passada in range(1, args.passadas + 1):
        if args.passadas > 1:
            print(f"\n--- passada {passada}/{args.passadas} "
                  f"({time.strftime('%H:%M:%S')})")
        resumo = [coletar_uf(cliente, eleicao, uf, args.pasta, args.ignorar_aux)
                  for uf in ufs]

        principais = sum(r["principais"] for r in resumo)
        publicadas = sum(r["publicadas"] for r in resumo)
        baixadas = sum(r["baixadas"] for r in resumo)
        sem_bu = sum(r["sem_bu"] for r in resumo)
        nao_totalizadas = sum(r["nao_totalizadas"] for r in resumo)
        print(f"\n  {baixadas:,} BUs de {principais:,} seções "
              f"({baixadas / max(principais, 1):.1%}) | {principais - publicadas:,} "
              f"ainda sem urna publicada | sem BU: {sem_bu:,} | "
              f"{(time.monotonic() - inicio) / 60:.1f} min no total")
        if nao_totalizadas:
            print(f"  {nao_totalizadas:,} seção(ões) baixadas antes da totalização final — "
                  f"se retransmitirem, o BU que vale muda")

        if publicadas >= principais and baixadas >= publicadas:
            print("  nada mais a baixar — todas as seções principais estão no zip")
            break
        if passada < args.passadas:
            print(f"  esperando {args.espera} min antes da próxima passada")
            time.sleep(args.espera * 60)


# As 27 unidades da federação mais o exterior (`zz`), que o TSE trata como uma
# abrangência a mais. O exterior não entra no site — não há endereço do CNEFE
# para casar, e o passo 22 descarta esses locais —, mas entra no total do Brasil
# que a conferência compara. Sem ele faltariam uns 300 mil votos de presidente e
# a guarda barraria a publicação com razão.
UFS = ["ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt",
       "pa", "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to",
       "zz"]


if __name__ == "__main__":
    main()

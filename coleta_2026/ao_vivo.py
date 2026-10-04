"""Os arquivos estáticos de que a camada ao vivo do site precisa.

Durante a apuração o site mostra 2026 buscando o resultado **direto no servidor
do TSE, no navegador de quem consulta** — sem passar pelo nosso servidor e sem
esperar o pipeline. O TSE libera CORS nesses arquivos, inclusive no binário do
boletim, e o limite de requisições é por IP, então cada visitante gasta o próprio
orçamento.

Para isso o navegador precisa de três coisas que o TSE não entrega prontas:

1. **Quais seções pertencem a cada região.** O TSE indexa o arquivo de urna por
   município/zona/seção; a nossa pergunta é por região, que é um grupo de locais
   na mesma coordenada. Só nós sabemos fazer essa ponte.
2. **Quem é o candidato de cada número.** O boletim traz o número digitado, não o
   nome.
3. **Os parâmetros da eleição e um interruptor.** Nada de código fixo, e um
   arquivo que se apaga para desligar tudo.

    python coleta_2026/ao_vivo.py
    python coleta_2026/ao_vivo.py --uf RR            # ensaio com uma UF
    python coleta_2026/ao_vivo.py --desligar         # só vira o interruptor

Saídas, todas em `publicado/ao_vivo/`:

    config.json                parâmetros da eleição e o interruptor
    secoes/{ibge}.json         região → seções, e o código TSE do município
    candidatos/presidente.json número → nome e partido
    candidatos/{UF}.json       o mesmo, para deputado federal

Apagar a pasta desliga a camada ao vivo e devolve o site ao comportamento de
antes — é o freio de mão, e não depende de republicar nada.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import converter  # noqa: E402
import tse  # noqa: E402

p10 = __import__("10_locais_oficiais")

DESTINO = config.DIR_PUBLICADO / "ao_vivo"

# Os cargos que o site publica. Os dois vêm no mesmo boletim, então mostrar os
# dois não custa nenhuma requisição a mais. O nome é o mesmo que o passo 40 usa
# nos JSON publicados, para a camada ao vivo encaixar sem tradução.
CARGOS_PADRAO = [1, 6]


def nome_do_cargo(codigo: int) -> str:
    return converter.CARGOS[codigo].lower().replace(" ", "_")

UFS = ["ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt",
       "pa", "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to"]


def secoes_principais(cliente: tse.Cliente, eleicao: tse.Eleicao,
                      ufs: list[str]) -> set[tuple[str, str, int]]:
    """(município, zona, seção) das seções que têm urna própria.

    Seção agregada não tem arquivo de urna: pedir o dela daria 404, e 404 arrisca
    bloqueio — agora no IP de quem está visitando o site. Por isso a lista é
    filtrada aqui, antes de chegar ao navegador.
    """
    saida: set[tuple[str, str, int]] = set()
    for uf in ufs:
        configuracao = cliente.json(cliente.caminho_config_secoes(eleicao, uf))
        for secao in tse.secoes_da_uf(configuracao):
            saida.add((secao["municipio"], secao["zona"], int(secao["secao"])))
        print(f"  {uf.upper()}: {len(saida):,} seções principais acumuladas")
    return saida


def montar_mapa(bruto: pd.DataFrame, de_para: pd.DataFrame, dim: pd.DataFrame,
                principais: set[tuple[str, str, int]] | None) -> dict[str, dict]:
    """Por município do IBGE: região → lista de [zona, local, seção]."""
    df = p10.preparar(bruto)
    df["nr_secao"] = df["NR_SECAO"].astype(int)

    regiao_do_local = dict(zip(de_para["id_local_votacao"], de_para["id_regiao"]))
    municipio_da_regiao = dict(zip(dim["id_regiao"], dim["cd_municipio_ibge"]))

    saida: dict[str, dict] = {}
    fora_da_malha = agregadas = 0
    for linha in df.itertuples():
        regiao = regiao_do_local.get(linha.id_local_votacao)
        if regiao is None:
            fora_da_malha += 1
            continue
        if principais is not None and (
                linha.cd_municipio_tse, f"{linha.nr_zona:04d}", linha.nr_secao) not in principais:
            agregadas += 1
            continue
        ibge = municipio_da_regiao.get(regiao)
        if ibge is None:
            fora_da_malha += 1
            continue
        municipio = saida.setdefault(ibge, {
            "uf": linha.SG_UF.strip().lower(),
            "municipio_tse": linha.cd_municipio_tse,
            "regioes": {},
        })
        municipio["regioes"].setdefault(str(regiao), []).append(
            [linha.nr_zona, linha.nr_local, linha.nr_secao])

    for municipio in saida.values():
        for regiao, secoes in municipio["regioes"].items():
            municipio["regioes"][regiao] = sorted(secoes)

    total = sum(len(s) for m in saida.values() for s in m["regioes"].values())
    print(f"  {len(saida):,} municípios | {total:,} seções mapeadas "
          f"| {agregadas:,} agregadas fora | {fora_da_malha:,} sem região")
    return saida


def montar_candidatos(ano: int, pasta: Path | None = None,
                      cargos: list[int] | None = None) -> dict[str, dict]:
    """Número → nome e partido, por cargo e unidade eleitoral.

    Reaproveita o cadastro do conversor, inclusive a escolha entre duas
    candidaturas com o mesmo número — o mesmo nome que vai ao ar na publicação
    definitiva vai ao ar na camada ao vivo.
    """
    cargos = cargos or CARGOS_PADRAO
    cadastro = converter.cadastro_de_candidatos(ano, pasta)
    saida: dict[str, dict] = {"presidente": {}}
    for codigo in cargos:
        nome_cargo = nome_do_cargo(codigo)
        cargo = converter.CARGOS[codigo]
        parte = cadastro[cadastro["cargo"] == cargo]
        for linha in parte.itertuples():
            # `.title()` é o mesmo tratamento do passo 40, para o nome do ano ao
            # vivo não destoar em maiúsculas do nome dos anos publicados.
            registro = {"nome": str(linha.NM_URNA_CANDIDATO).title(),
                        "partido": linha.SG_PARTIDO}
            if nome_cargo == "presidente":
                saida["presidente"][str(linha.NR_CANDIDATO)] = registro
            else:
                saida.setdefault(linha.SG_UE, {})[str(linha.NR_CANDIDATO)] = registro
    ufs = [chave for chave in saida if chave != "presidente"]
    print(f"  nacional: {len(saida['presidente'])} candidatos | "
          f"por UF: {len(ufs)} UFs, {sum(len(saida[uf]) for uf in ufs):,} candidatos")
    return saida


# Em qual espécie de eleição cada cargo é apurado. Presidente sai na federal;
# governador, senador e os dois deputados saem na estadual, porque a unidade
# eleitoral deles é a UF; prefeito e vereador, na municipal.
ESPECIE_DO_CARGO = {1: "federal", 3: "estadual", 5: "estadual", 6: "estadual",
                    7: "estadual", 11: "municipal", 13: "municipal"}


def eleicoes_por_cargo(cliente: tse.Cliente, ano: int, turno: int,
                       cargos: list[int], federal: tse.Eleicao) -> dict[str, str]:
    """Código da eleição de cada cargo pedido, descoberto no ele-c.json.

    A federal já vem resolvida por quem chamou; as outras saem da mesma lista,
    pelo nome, como `eleicao_federal` faz. Cargo cuja eleição não aparece fica
    de fora, e o navegador cai no padrão — melhor do que apontar para a errada.
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


def escrever(caminho: Path, conteudo: dict) -> int:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(conteudo, ensure_ascii=False, separators=(",", ":"))
    caminho.write_text(texto, encoding="utf-8")
    return len(texto.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ano", type=int, default=2026)
    parser.add_argument("--turno", type=int, default=1)
    parser.add_argument("--uf", nargs="+", help="só estas UFs (ensaio)")
    parser.add_argument("--eleicao", help="código da eleição; o padrão é descobrir no TSE")
    parser.add_argument("--ciclo", help="ensaio: ciclo explícito")
    parser.add_argument("--pleito", help="ensaio: pleito explícito")
    parser.add_argument("--candidatos", type=Path)
    parser.add_argument("--cargo", type=int, nargs="+", default=CARGOS_PADRAO,
                        help="códigos de cargo (ensaio com 2024: 11 13)")
    parser.add_argument("--locais", type=Path,
                        help="zip do eleitorado por seção (padrão: o do ano, em "
                             "dados/bruto/locais_oficiais)")
    parser.add_argument("--sem-secoes", action="store_true",
                        help="não consulta o TSE; inclui também as seções agregadas")
    parser.add_argument("--desligar", action="store_true",
                        help="só grava config.json com ativo=false")
    parser.add_argument("--destino", type=Path,
                        help="outra pasta de saída (o teste usa, para não "
                             "misturar com o que vai ao ar)")
    args = parser.parse_args()

    global DESTINO
    if args.destino:
        DESTINO = args.destino

    if args.desligar:
        escrever(DESTINO / "config.json", {"ativo": False})
        print(f"camada ao vivo desligada em {DESTINO / 'config.json'}")
        return

    cliente = tse.Cliente()
    if args.eleicao:
        eleicao = tse.Eleicao(codigo=args.eleicao, nome="(informada na linha de comando)",
                              turno=str(args.turno), ciclo=args.ciclo, pleito=args.pleito,
                              data="")
    else:
        eleicao = cliente.eleicao_federal(args.ano, args.turno)
    print(f"=== estáticos da camada ao vivo — {eleicao.nome}")
    print(f"    eleição {eleicao.codigo} | pleito {eleicao.pleito} | ciclo {eleicao.ciclo}")

    ufs = [u.lower() for u in (args.uf or UFS)]
    principais = None if args.sem_secoes else secoes_principais(cliente, eleicao, ufs)

    caminho = args.locais or (config.DIR_BRUTO / "locais_oficiais"
                              / f"eleitorado_local_votacao_{args.ano}.zip")
    bruto = p10.ler_arquivo(caminho)
    if args.uf:
        bruto = bruto[bruto["SG_UF"].str.strip().str.lower().isin(set(ufs))]
    de_para = pd.read_parquet(config.DIR_INTERMEDIARIO / "de_para_local_regiao.parquet")
    dim = pd.read_parquet(config.DIR_INTERMEDIARIO / "dim_regiao.parquet",
                          columns=["id_regiao", "cd_municipio_ibge"])

    mapa = montar_mapa(bruto, de_para, dim, principais)
    bytes_secoes = sum(escrever(DESTINO / "secoes" / f"{ibge}.json", conteudo)
                       for ibge, conteudo in mapa.items())
    print(f"  secoes/: {len(mapa):,} arquivos, {bytes_secoes / 1e6:.1f} MB")

    candidatos = montar_candidatos(args.ano, args.candidatos, args.cargo)
    bytes_cand = escrever(DESTINO / "candidatos" / "presidente.json", candidatos["presidente"])
    for chave, lista in candidatos.items():
        if chave != "presidente":
            bytes_cand += escrever(DESTINO / "candidatos" / f"{chave}.json", lista)
    print(f"  candidatos/: {len(candidatos):,} arquivos, {bytes_cand / 1e6:.1f} MB")

    escrever(DESTINO / "config.json", {
        "ativo": True,
        "ambiente": cliente.ambiente,
        "ciclo": eleicao.ciclo,
        "pleito": eleicao.pleito,
        "eleicao": eleicao.codigo,
        "turno": str(args.turno),
        "ano": str(args.ano),
        # A data do pleito deixa a camada dormente até o dia: antes dela não há
        # o que buscar, e "nenhuma urna enviou boletim" na véspera só confunde.
        "data": eleicao.data,
        "cargos": {str(codigo): nome_do_cargo(codigo) for codigo in args.cargo},
        # Cada cargo é apurado dentro de uma eleição, e o endereço do resultado
        # agregado leva esse código. Sem este mapa o navegador pedia deputado
        # federal na eleição do presidente e recebia 404 — a comparação com
        # município, estado e Brasil ficava vazia.
        "eleicoes_por_cargo": eleicoes_por_cargo(cliente, args.ano, args.turno,
                                                 args.cargo, eleicao),
        "base": tse.BASE,
    })
    print(f"\n  pronto. Para desligar: apague {DESTINO} ou rode com --desligar")


if __name__ == "__main__":
    main()

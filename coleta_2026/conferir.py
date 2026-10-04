"""Confere o que lemos dos boletins contra o resultado oficial do TSE.

É a guarda que autoriza ou barra a publicação. O parser pode somar certo e ler o
candidato errado; a coleta pode ter perdido seções; a conversão pode ter trocado
um partido. Nada disso aparece olhando só para os nossos números — só aparece
comparando com quem apurou.

    python coleta_2026/conferir.py --eleicao 6257 --cargo 1            # presidente, Brasil
    python coleta_2026/conferir.py --eleicao 6257 --cargo 6 --nivel uf # dep. federal, por UF

Sai com código 1 se houver divergência — para servir de porteiro antes do deploy.

Como os dois números são feitos comparáveis
-------------------------------------------
O BU registra o que a urna gravou; a totalização **reclassifica** alguns votos. O
arquivo oficial não esconde isso, publica cada parcela separada, e por isso a
comparação pode ser exata em vez de tolerante:

    tv (total de votos) = vvc (válidos computados) + vb (brancos) + tvn (nulos)

    vvc = vnom (nominais) + vl (legenda) + van (anulados) + vansj (anulados sub judice)
    tvn = vn (nulos de urna) + vnt (nominais que a totalização virou nulo)

A urna não sabe que uma candidatura foi anulada: grava voto nominal. Quem move o
voto para outra prateleira é a totalização. Então o nosso total de válidos tem de
bater com `vvc + vnt`, e o nosso de nulos com `tvn - vnt` — e bate no dígito.

Exemplo real (prefeito, São João da Baliza/RR, 2024): o oficial mostra o nº 15
com 836 votos na lista de candidatos e um `vnom` de 4.009 que **não** os inclui,
porque estão em `vansj`. Comparar com `vnom` acusaria 836 votos de erro nosso
onde não há erro nenhum; comparar com `vvc` fecha em zero.

A única frouxidão que resta é para seção que não transmitiu: aí faltam votos do
nosso lado da conta, e o limite está em `TOLERANCIA_FALTA`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tse

RAIZ = Path(__file__).resolve().parents[1]
PADRAO = RAIZ / "dados" / "intermediario" / "votos_local_votacao_2026.parquet"

CARGO_POR_CODIGO = {1: "PRESIDENTE", 6: "DEPUTADO FEDERAL", 11: "PREFEITO", 13: "VEREADOR"}

# Quanto do comparecimento oficial pode faltar do nosso lado por seção sem BU
# transmitido. Acima disto não se publica.
TOLERANCIA_FALTA = 0.0005      # 0,05%

# Por votável a conta também é exata; a folga é só colchão para recorte grande.
TOLERANCIA_VOTOS = 10
TOLERANCIA_RELATIVA = 0.0001   # 0,01%

TOTAIS = ("comparecimento", "validos", "brancos", "nulos")


def oficial(cliente: tse.Cliente, eleicao: tse.Eleicao, cargo: int, nivel: str,
            uf: str | None = None, municipio: str | None = None) -> dict | None:
    sufixo = f"c{cargo:04d}-{eleicao.sufixo_eleicao}-u.json"
    if nivel == "br":
        caminho = f"{cliente.ambiente}/{eleicao.ciclo}/{eleicao.codigo}/dados/br/br-{sufixo}"
    elif nivel == "uf":
        caminho = f"{cliente.ambiente}/{eleicao.ciclo}/{eleicao.codigo}/dados/{uf}/{uf}-{sufixo}"
    else:
        caminho = (f"{cliente.ambiente}/{eleicao.ciclo}/{eleicao.codigo}/dados/{uf}"
                   f"/{uf}{municipio}-{sufixo}")
    return cliente.json(caminho, opcional=True)


def votaveis_do_oficial(conteudo: dict) -> tuple[dict, dict]:
    """Número -> (nome, votos), separando candidato de legenda.

    Separar importa porque o nosso voto de legenda é creditado ao número do
    partido (dois dígitos) e o nominal ao número do candidato (cinco), e no
    oficial eles vivem em campos diferentes: `vap` no candidato, `tvtl` no
    partido.
    """
    nominais: dict[str, tuple[str, int]] = {}
    legendas: dict[str, tuple[str, int]] = {}
    for cargo in conteudo.get("carg", []):
        for agremiacao in cargo.get("agr", []):
            for partido in agremiacao.get("par", []):
                if int(partido.get("tvtl", 0)):
                    legendas[str(partido["n"])] = (f"{partido.get('sg', '')} (legenda)",
                                                   int(partido["tvtl"]))
                for candidato in partido.get("cand", []):
                    nominais[str(candidato["n"])] = (candidato.get("nmu", ""),
                                                     int(candidato.get("vap", 0)))
    return nominais, legendas


def totais_do_oficial(conteudo: dict) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Os totais oficiais já traduzidos para o que a urna gravou (ver docstring)."""
    votos = {chave: int(valor) for chave, valor in conteudo.get("v", {}).items()
             if not chave.startswith("p")}
    eleitorado = conteudo.get("e", {})
    nulos_totais = votos.get("tvn", votos.get("vn", 0))
    reclassificados = votos.get("vnt", 0)

    totais = {
        "comparecimento": int(eleitorado.get("c", 0)),
        "validos": votos.get("vvc", 0) + reclassificados,
        "brancos": votos.get("vb", 0),
        "nulos": nulos_totais - reclassificados,
    }
    ajustes = {chave: votos.get(chave, 0) for chave in ("vl", "van", "vansj", "vnt")}

    # Se a identidade do arquivo não fechar, é o nosso entendimento do formato que
    # está errado — e aí a comparação toda perde sentido. Melhor dizer.
    avisos = []
    soma = votos.get("vvc", 0) + votos.get("vb", 0) + nulos_totais
    total_declarado = votos.get("tv", soma)
    if total_declarado != soma:
        avisos.append(f"o arquivo oficial não fecha: tv {total_declarado:,} "
                      f"diferente de vvc+vb+tvn {soma:,}")
    if totais["comparecimento"] != total_declarado:
        avisos.append(f"comparecimento {totais['comparecimento']:,} "
                      f"diferente do total de votos {total_declarado:,}")
    return totais, ajustes, avisos


def nossos_numeros(df: pd.DataFrame) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    def por_numero(tipo: str) -> dict[str, int]:
        parte = df[df["tipo_voto"] == tipo]
        return {str(numero): int(votos) for numero, votos in
                parte.groupby("nr_votavel")["qt_votos"].sum().items()}

    por_tipo = df.groupby("tipo_voto")["qt_votos"].sum().to_dict()
    totais = {
        "comparecimento": int(df["qt_votos"].sum()),
        "validos": int(por_tipo.get("nominal", 0) + por_tipo.get("legenda", 0)),
        "brancos": int(por_tipo.get("branco", 0)),
        "nulos": int(por_tipo.get("nulo", 0)),
    }
    return por_numero("nominal"), por_numero("legenda"), totais


def conferir_totais(nossos: dict[str, int], deles: dict[str, int],
                    ajustes: dict[str, int], rotulo: str) -> list[str]:
    """Compara os quatro totais. Esperado: zero de diferença em todos."""
    problemas = []
    falta = deles["comparecimento"] - nossos["comparecimento"]

    for chave in TOTAIS:
        diferenca = nossos[chave] - deles[chave]
        # Seção sem BU transmitido tira votos de todas as prateleiras ao mesmo
        # tempo: diferença negativa até o tamanho do que falta é explicada.
        explicada = falta > 0 and -falta <= diferenca <= 0
        marca = " " if diferenca == 0 else ("~" if explicada else "x")
        print(f"   {marca} {chave:>15}: oficial {deles[chave]:>12,} "
              f"nosso {nossos[chave]:>12,}  dif {diferenca:+,}")
        if marca == "x":
            problemas.append(f"{rotulo}: {chave} difere em {diferenca:+,} "
                             f"(não é seção faltando)")

    if falta > 0:
        fracao = falta / max(deles["comparecimento"], 1)
        print(f"     faltam {falta:,} votos nossos — {fracao:.4%} do comparecimento")
        if fracao > TOLERANCIA_FALTA:
            problemas.append(f"{rotulo}: faltam {falta:,} votos ({fracao:.4%}), "
                             f"acima do limite de {TOLERANCIA_FALTA:.2%}")
    elif falta < 0:
        problemas.append(f"{rotulo}: temos {-falta:,} votos a mais que o oficial")

    if any(ajustes.values()):
        print("     reclassificações do oficial: "
              + ", ".join(f"{k}={v:,}" for k, v in ajustes.items() if v))
    return problemas


def comparar_votaveis(nosso: dict[str, int], deles: dict[str, tuple[str, int]],
                      rotulo: str, titulo: str) -> list[str]:
    if not deles and not nosso:
        return []
    problemas = []
    print(f"     {titulo}")
    for numero, (nome, votos_oficiais) in sorted(deles.items(), key=lambda kv: -kv[1][1]):
        nossos = nosso.get(numero, 0)
        diferenca = nossos - votos_oficiais
        limite = max(TOLERANCIA_VOTOS, votos_oficiais * TOLERANCIA_RELATIVA)
        marca = " " if abs(diferenca) <= limite else "x"
        print(f"   {marca} {numero:>5} {nome[:28]:28} oficial {votos_oficiais:>12,} "
              f"nosso {nossos:>12,}  dif {diferenca:+,}")
        if abs(diferenca) > limite:
            problemas.append(f"{rotulo}, nº {numero} ({nome}): diferença de {diferenca:+,} votos")

    extras = {n: v for n, v in nosso.items() if n not in deles and v > TOLERANCIA_VOTOS}
    for numero, votos in sorted(extras.items(), key=lambda kv: -kv[1]):
        print(f"   x {numero:>5} {'(não está no resultado oficial)':28} oficial {0:>12,} "
              f"nosso {votos:>12,}")
        problemas.append(f"{rotulo}, nº {numero}: {votos:,} votos nossos e nenhum no oficial")
    return problemas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eleicao", required=True)
    parser.add_argument("--cargo", type=int, default=1)
    parser.add_argument("--nivel", choices=["br", "uf", "municipio"], default="br")
    parser.add_argument("--ciclo", default="ele2026")
    parser.add_argument("--pleito", default="3220")
    parser.add_argument("--ano", type=int, default=2026)
    parser.add_argument("--turno", type=int, default=1)
    parser.add_argument("--votos", type=Path, default=PADRAO)
    parser.add_argument("--uf", nargs="+", help="recorte, quando o nível não é br")
    parser.add_argument("--resumido", action="store_true",
                        help="só os totais, sem a lista de votáveis")
    args = parser.parse_args()

    cliente = tse.Cliente()
    eleicao = tse.Eleicao(codigo=args.eleicao, nome="", turno=str(args.turno),
                          ciclo=args.ciclo, pleito=args.pleito, data="")

    if not args.votos.exists():
        sys.exit(f"não achei {args.votos} — a conversão dos boletins ainda não rodou")
    df = pd.read_parquet(args.votos)
    df = df[(df["ano_eleicao"] == args.ano) & (df["turno"] == str(args.turno))
            & (df["cargo"] == CARGO_POR_CODIGO[args.cargo])]
    if df.empty:
        sys.exit(f"nenhum voto de {CARGO_POR_CODIGO[args.cargo]} em {args.ano} "
                 f"dentro de {args.votos}")

    print(f"=== conferência contra o TSE — {CARGO_POR_CODIGO[args.cargo]}, nível {args.nivel}")
    problemas: list[str] = []

    if args.nivel == "br":
        recortes = [("Brasil", df, {})]
    elif args.nivel == "uf":
        ufs = [u.upper() for u in (args.uf or sorted(df["sg_uf"].unique()))]
        recortes = [(uf, df[df["sg_uf"] == uf], {"uf": uf.lower()}) for uf in ufs]
    else:
        df = df.assign(municipio=df["id_local_votacao"].str.split("_").str[1])
        recortes = [(f"{uf}/{mun}", grupo, {"uf": uf.lower(), "municipio": mun})
                    for (uf, mun), grupo in df.groupby(["sg_uf", "municipio"])]

    for rotulo, parte, onde in recortes:
        conteudo = oficial(cliente, eleicao, args.cargo, args.nivel, **onde)
        print(f"\n  {rotulo}")
        if conteudo is None:
            print("     o TSE ainda não publicou este arquivo")
            problemas.append(f"{rotulo}: sem arquivo oficial para comparar")
            continue

        deles_totais, ajustes, avisos = totais_do_oficial(conteudo)
        for aviso in avisos:
            print(f"   x {aviso}")
            problemas.append(f"{rotulo}: {aviso}")

        nosso_nominal, nosso_legenda, nossos_totais = nossos_numeros(parte)
        problemas += conferir_totais(nossos_totais, deles_totais, ajustes, rotulo)

        if not args.resumido:
            deles_nominal, deles_legenda = votaveis_do_oficial(conteudo)
            problemas += comparar_votaveis(nosso_nominal, deles_nominal, rotulo, "por candidato")
            problemas += comparar_votaveis(nosso_legenda, deles_legenda, rotulo, "por legenda")

    print()
    if problemas:
        print(f"  {len(problemas)} divergência(s):")
        for problema in problemas[:20]:
            print(f"    - {problema}")
        if len(problemas) > 20:
            print(f"    ... e outras {len(problemas) - 20}")
        sys.exit(1)
    print("  tudo conferido — pode publicar")


if __name__ == "__main__":
    main()

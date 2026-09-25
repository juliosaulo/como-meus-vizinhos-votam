"""Distância entre cada endereço e a região a que ele foi atribuído.

O vizinho mais próximo sempre devolve alguém: se um local de votação tiver
coordenada errada, ele continua sendo "o mais próximo" de algum endereço — só
que a quilômetros de distância. Nenhuma guarda do pipeline enxerga isso, porque
não há resposta ausente para reclamar.

Este diagnóstico usa o resumo que o passo 31 grava por região (contagem, soma e
máximo da distância, e quantos endereços passam de 5 km e de 25 km) para
responder duas perguntas:

- **quão longe as pessoas moram do seu local de votação**, em geral — que é uma
  característica real do país, não um defeito: no interior, dezenas de
  quilômetros são normais;
- **quais regiões destoam**, concentrando endereços muito distantes — as
  candidatas a coordenada errada, que valem uma conferência manual.

Saída: `publicado/diagnostico_distancias.json`

Uso:
    python qualidade/diagnostico_distancias.py [--top 30]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def carregar() -> pd.DataFrame:
    arquivos = [config.DIR_DISTANCIAS_REGIAO / f"{uf}.parquet" for uf in config.ufs_para_processar()]
    existentes = [a for a in arquivos if a.exists()]
    if not existentes:
        raise FileNotFoundError(
            f"nenhum resumo de distância em {config.DIR_DISTANCIAS_REGIAO} — rode o passo 31."
        )
    if len(existentes) < len(arquivos):
        faltando = [a.stem for a in arquivos if not a.exists()]
        print(f"  [aviso] sem resumo de distância para: {', '.join(faltando)}")

    dist = pd.concat([pd.read_parquet(a) for a in existentes], ignore_index=True)
    dim = pd.read_parquet(
        config.DIR_INTERMEDIARIO / "dim_regiao.parquet",
        columns=["id_regiao", "sg_uf", "cd_municipio_ibge", "nm_municipio", "nm_local_votacao",
                 "ds_endereco"],
    )
    return dist.merge(dim, on="id_regiao", how="left")


def resumir(df: pd.DataFrame) -> dict:
    total = int(df["n_enderecos"].sum())
    return {
        "enderecos": total,
        "distancia_media_m": round(float(df["soma_dist"].sum() / total), 1),
        "pct_acima_5km": round(float(df["acima_5km"].sum() / total * 100), 2),
        "pct_acima_25km": round(float(df["acima_25km"].sum() / total * 100), 3),
        "regioes": int(len(df)),
        "regioes_com_endereco_acima_25km": int((df["acima_25km"] > 0).sum()),
        "distancia_maxima_km": round(float(df["dist_max"].max() / 1000), 1),
        "regioes_com_endereco_na_coordenada_exata": int((df["dist_min"] == 0).sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=30, help="quantas regiões suspeitas listar")
    args = parser.parse_args()

    print("=== diagnóstico de distâncias ===")
    df = carregar()
    df["distancia_media_m"] = df["soma_dist"] / df["n_enderecos"]

    nacional = resumir(df)
    por_uf = (
        df.groupby("sg_uf").apply(resumir, include_groups=False)
        .apply(pd.Series).reset_index().to_dict("records")
    )

    def listar(recorte: pd.DataFrame) -> list[dict]:
        return [
            {
                "id_regiao": int(r.id_regiao), "uf": r.sg_uf, "municipio": r.nm_municipio,
                "local": r.nm_local_votacao, "endereco": r.ds_endereco,
                "enderecos": int(r.n_enderecos),
                "acima_25km": int(r.acima_25km),
                "distancia_media_km": round(float(r.distancia_media_m) / 1000, 1),
                "distancia_minima_m": round(float(r.dist_min)),
                "distancia_maxima_km": round(float(r.dist_max) / 1000, 1),
                "vezes_a_mediana_do_municipio": round(float(r.razao_municipio), 1),
            }
            for r in recorte.itertuples()
        ]

    # Distância absoluta mede sobretudo o tamanho do município: em Barcelos (AM),
    # maior que Portugal e com 6 regiões, centenas de quilômetros são o normal.
    # O sinal que aponta coordenada errada é relativo — a região que destoa das
    # outras do próprio município, onde as demais dão conta de perto.
    df["razao_municipio"] = (
        df["distancia_media_m"] / df.groupby("cd_municipio_ibge")["distancia_media_m"].transform("median")
    ).replace([float("inf")], 0).fillna(0)

    # Invariante: a coordenada de cada região veio de um endereço do CNEFE, então
    # esse endereço está a 0 m dela. Hoje isso vale para 100% das regiões, e por
    # isso a distância mínima não separa nada. Ela passa a separar quando entrar
    # coordenada de outra fonte — por exemplo a latitude/longitude do próprio
    # TSE, para locais novos: aí um valor alto é coordenada a conferir.
    sem_vizinho = df[df["dist_min"] > 100].sort_values("dist_min", ascending=False).head(args.top)

    mais_distantes = df[df["acima_25km"] > 0].sort_values(
        ["acima_25km", "dist_max"], ascending=False
    ).head(args.top)
    destoantes = (
        df[(df["n_enderecos"] >= 100) & (df["distancia_media_m"] >= 5_000)]
        .sort_values("razao_municipio", ascending=False).head(args.top)
    )
    lista, lista_destoantes = listar(mais_distantes), listar(destoantes)
    lista_sem_vizinho = listar(sem_vizinho)

    destino = config.DIR_PUBLICADO / "diagnostico_distancias.json"
    destino.write_text(
        json.dumps({"nacional": nacional, "por_uf": por_uf,
                    "regioes_sem_endereco_na_coordenada": lista_sem_vizinho,
                    "regioes_mais_distantes": lista,
                    "regioes_que_destoam_do_municipio": lista_destoantes},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"  distância média endereço → local: {nacional['distancia_media_m']:,.0f} m")
    print(f"  endereços a mais de 5 km: {nacional['pct_acima_5km']}%")
    print(f"  endereços a mais de 25 km: {nacional['pct_acima_25km']}%")
    print(f"  regiões com algum endereço além de 25 km: {nacional['regioes_com_endereco_acima_25km']:,}"
          f" de {nacional['regioes']:,}")
    print(f"  maior distância observada: {nacional['distancia_maxima_km']:,.1f} km")
    na_coordenada = nacional["regioes_com_endereco_na_coordenada_exata"]
    print(f"  regiões com endereço do CNEFE na coordenada exata: {na_coordenada:,} "
          f"de {nacional['regioes']:,}")
    if lista_sem_vizinho:
        print("\n  sem endereço na coordenada (coordenada de outra fonte, a conferir):")
        for r in lista_sem_vizinho[:10]:
            print(f"    endereço mais próximo a {r['distancia_minima_m']:>7,} m | "
                  f"{r['enderecos']:>6,} end. | {r['municipio']}/{r['uf']} — {r['local']}")
    if lista:
        print("\n  mais endereços distantes (em geral, município grande com poucos locais):")
        for r in lista[:5]:
            print(f"    {r['acima_25km']:>7,} end. além de 25 km | máx {r['distancia_maxima_km']:>6.1f} km"
                  f" | {r['municipio']}/{r['uf']} — {r['local']}")
    if lista_destoantes:
        print("\n  regiões que destoam do próprio município (candidatas a coordenada errada):")
        for r in lista_destoantes[:10]:
            print(f"    {r['vezes_a_mediana_do_municipio']:>6.1f}× a mediana do município | "
                  f"média {r['distancia_media_km']:>6.1f} km | {r['enderecos']:>6,} end. | "
                  f"{r['municipio']}/{r['uf']} — {r['local']}")
    print(f"\n  salvo: {destino}")


if __name__ == "__main__":
    main()

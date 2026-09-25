"""Gera `publicado/cobertura.json` — o que o dado cobre e o que não cobre.

Nenhuma etapa deste pipeline cobre 100% do que se propõe, e cada perda tem uma
causa diferente. Este relatório junta todas num lugar só para que o site possa
mostrá-las ao usuário em vez de apresentar um número como se fosse exato.

São três perdas independentes:

1. **Geocodificação** — locais de votação sem coordenada, porque o nome e o
   endereço no cadastro do TSE não permitiram achar o ponto (ver
   `dados_importados/PROVENIENCIA.md`).
2. **Voto fora da malha** — votos dados em locais sem coordenada, que por isso
   não entram em região nenhuma.
3. **Índice de endereços** — endereços cuja rua e número não bastam para
   distinguir a região, sobretudo os sem número.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def cobertura_geocodificacao() -> dict:
    locais = pd.read_parquet(config.ARQ_LOCAIS_GEOCODIFICADOS)
    locais = locais[locais["sg_uf"] != "ZZ"]
    if config.UFS_ALVO:
        locais = locais[locais["sg_uf"].isin(config.UFS_ALVO)]
    com = locais["latitude_final"].notna()

    por_uf = (
        locais.assign(tem=com)
        .groupby("sg_uf")
        .agg(locais=("id_local_votacao", "count"), com_coordenada=("tem", "sum"))
    )
    por_uf["pct"] = (por_uf["com_coordenada"] / por_uf["locais"] * 100).round(1)

    return {
        "locais_votacao": int(len(locais)),
        "com_coordenada": int(com.sum()),
        "pct": round(float(com.mean() * 100), 1),
        "por_uf": por_uf.reset_index().to_dict("records"),
        "nota": (
            "Locais sem coordenada não são erro de pareamento: em geral o cadastro do TSE "
            "não trazia nome ou endereço aproveitável. Ver dados_importados/PROVENIENCIA.md."
        ),
    }


def cobertura_voto() -> dict:
    votos_local = pd.read_parquet(
        config.DIR_INTERMEDIARIO / "votos_local_votacao.parquet", columns=["qt_votos"]
    )
    votos_regiao = pd.read_parquet(
        config.DIR_PUBLICADO / "votos_regiao.parquet", columns=["qt_votos", "id_regiao"]
    )
    total = int(votos_local["qt_votos"].sum())
    dentro = int(votos_regiao["qt_votos"].sum())
    return {
        "votos_apurados": total,
        "votos_em_regiao": dentro,
        "pct": round(dentro / total * 100, 1),
        "regioes_com_voto": int(votos_regiao["id_regiao"].nunique()),
        "nota": (
            "A diferença são votos dados em locais sem coordenada. Totais eleitorais "
            "oficiais não devem ser lidos desta base — ela cobre só o que é espacializável."
        ),
    }


def cobertura_enderecos() -> dict:
    arquivos = [config.DIR_ENDERECOS_REGIAO / f"{uf}.parquet" for uf in config.ufs_para_processar()]
    arquivos = [a for a in arquivos if a.exists()]
    if not arquivos:
        return {}

    # Uma UF por vez: as contagens são somáveis, e o país inteiro não cabe na memória.
    total = sem_numero = em_chave_ambigua = 0
    for arquivo in arquivos:
        df = pd.read_parquet(arquivo)
        total += int(df["n_enderecos"].sum())
        sem_numero += int(df.loc[df["numero"] == 0, "n_enderecos"].sum())

        por_chave = df.groupby(["cd_municipio_ibge", "logradouro", "numero"])["id_regiao"].nunique()
        peso = df.groupby(["cd_municipio_ibge", "logradouro", "numero"])["n_enderecos"].sum()
        ambiguas = por_chave[por_chave > 1].index
        em_chave_ambigua += int(peso[peso.index.isin(ambiguas)].sum())
        del df

    validacao = config.DIR_PUBLICADO / "validacao_indice_ruas.json"
    acerto = None
    caminhos: dict[str, float] = {}
    if validacao.exists():
        registros = json.loads(validacao.read_text(encoding="utf-8"))
        if registros:
            acerto = round(sum(r["acerto_pct"] for r in registros) / len(registros), 2)
            # O acerto depende do que o usuário informa — o site mostra isso ao
            # lado da resposta, em vez de um número único que não existe.
            for chave in ("so_rua", "rua_bairro", "rua_numero", "completo"):
                for sufixo in ("", "_domicilios"):
                    coluna = f"acerto_{chave}{sufixo}_pct"
                    if coluna in registros[0]:
                        caminhos[f"{chave}{sufixo}"] = round(
                            sum(r[coluna] for r in registros) / len(registros), 2
                        )

    return {
        "enderecos": total,
        "sem_numero": sem_numero,
        "pct_sem_numero": round(sem_numero / total * 100, 1),
        "em_chave_ambigua": em_chave_ambigua,
        "pct_em_chave_ambigua": round(em_chave_ambigua / total * 100, 1),
        "acerto_medido_pct": acerto,
        "acerto_por_caminho_pct": caminhos,
        "nota": (
            "Endereço sem número (S/N) é a principal fonte de imprecisão: rua e número são "
            "a chave de consulta, e sem número todos os endereços da rua compartilham a "
            "mesma resposta. É mais comum em área rural."
        ),
    }


def main() -> None:
    print("=== relatório de cobertura ===")
    relatorio = {
        "recorte": {
            "ufs": config.ufs_para_processar(),
            "anos": config.ANOS_ELEICAO,
            "cargos": config.CARGOS_ALVO,
            "ano_referencia_malha": config.ANO_REFERENCIA_MALHA,
        },
        "geocodificacao": cobertura_geocodificacao(),
        "voto": cobertura_voto(),
        "enderecos": cobertura_enderecos(),
    }

    destino = config.DIR_PUBLICADO / "cobertura.json"
    destino.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")

    geo, voto, end = relatorio["geocodificacao"], relatorio["voto"], relatorio["enderecos"]
    print(f"  locais com coordenada: {geo['pct']}%")
    print(f"  votos dentro de alguma região: {voto['pct']}%")
    if end:
        print(f"  endereços sem número: {end['pct_sem_numero']}%")
        print(f"  endereços em chave ambígua: {end['pct_em_chave_ambigua']}%")
        print(f"  acerto medido do índice: {end['acerto_medido_pct']}%")
    print(f"  salvo: {destino}")


if __name__ == "__main__":
    main()

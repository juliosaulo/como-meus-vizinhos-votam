"""Passo 40 — monta os artefatos que o site consome.

Tudo estático e fatiado por município: escolher um estado baixa uma lista
pequena, escolher um município baixa dois arquivos daquele município e nada
mais. Não há servidor, banco nem API — o "backend" é o sistema de arquivos.

Arquivos gerados em `publicado/`:

    ufs.json                      estados
    municipios/{UF}.json          municípios do estado
    ruas/{cd_municipio}.json      ruas + índice de trechos para achar a região
                                  (e, nas ruas ambíguas, as regiões com peso e a
                                  quebra por bairro, que desempatam sem o número)
    bairros/{cd_municipio}.json   bairro → regiões, para quem não sabe o nome da rua
    regioes/{cd_municipio}.json   local de votação (nome, endereço, coordenada)
                                  e resultados de cada região
    cobertura.json                o que o dado cobre e o que não cobre

Os mesmos dados saem também em parquet, para quem quiser analisar em vez de
consumir no site.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

NOMES_UF = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso", "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco",
    "PI": "Piauí", "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RO": "Rondônia", "RR": "Roraima", "RS": "Rio Grande do Sul", "SC": "Santa Catarina",
    "SE": "Sergipe", "SP": "São Paulo", "TO": "Tocantins",
}


def escrever_json(caminho: Path, conteudo) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        # `allow_nan=False` é a guarda: por padrão o Python grava `NaN`, que não
        # é JSON válido e faz o `JSON.parse` do navegador falhar — o arquivo sai
        # do pipeline "certo" e quebra no site. Melhor falhar aqui.
        json.dump(conteudo, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def calcular_resultados(votos: pd.DataFrame) -> dict[int, dict]:
    """Percentuais por região, ano e cargo.

    O denominador é o voto VÁLIDO (nominal + legenda), a convenção legal
    brasileira — branco e nulo ficam fora do percentual dos candidatos e são
    reportados à parte, como proporção do comparecimento.
    """
    valido = votos["tipo_voto"].isin(["nominal", "legenda"])
    denominador = (
        votos[valido].groupby(["id_regiao", "ano_eleicao", "turno", "cargo"])["qt_votos"]
        .sum().rename("validos")
    )
    total = (
        votos.groupby(["id_regiao", "ano_eleicao", "turno", "cargo"])["qt_votos"]
        .sum().rename("total")
    )

    nominal = votos[votos["tipo_voto"] == "nominal"].copy()
    nominal = nominal.join(denominador, on=["id_regiao", "ano_eleicao", "turno", "cargo"])
    nominal["pct"] = (nominal["qt_votos"] / nominal["validos"] * 100).round(2)

    nao_nominal = (
        votos[votos["tipo_voto"].isin(["branco", "nulo"])]
        .groupby(["id_regiao", "ano_eleicao", "turno", "cargo", "tipo_voto"])["qt_votos"]
        .sum().reset_index()
        .join(total, on=["id_regiao", "ano_eleicao", "turno", "cargo"])
    )
    nao_nominal["pct"] = (nao_nominal["qt_votos"] / nao_nominal["total"] * 100).round(2)

    resultados: dict[int, dict] = {}

    for cargo, n_top in (("PRESIDENTE", None), ("DEPUTADO FEDERAL", config.N_TOP_DEPUTADOS)):
        do_cargo = nominal[nominal["cargo"] == cargo]
        chave_cargo = "presidente" if cargo == "PRESIDENTE" else "deputado_federal"

        for (id_regiao, ano, turno), grupo in do_cargo.groupby(["id_regiao", "ano_eleicao", "turno"]):
            grupo = grupo.sort_values("qt_votos", ascending=False)
            if n_top:
                grupo = grupo.head(n_top)
            candidatos = [
                {
                    "nome": str(linha.nm_votavel).title(),
                    "partido": str(linha.sg_partido) if pd.notna(linha.sg_partido) else None,
                    "votos": int(linha.qt_votos),
                    "pct": float(linha.pct),
                }
                for linha in grupo.itertuples()
            ]
            no = resultados.setdefault(int(id_regiao), {})
            no.setdefault(chave_cargo, {}).setdefault(str(ano), {})[str(turno)] = candidatos

    for linha in nao_nominal.itertuples():
        chave_cargo = "presidente" if linha.cargo == "PRESIDENTE" else "deputado_federal"
        no = resultados.setdefault(int(linha.id_regiao), {})
        alvo = no.setdefault("nao_nominal", {}).setdefault(chave_cargo, {}) \
                 .setdefault(str(linha.ano_eleicao), {}).setdefault(str(linha.turno), {})
        alvo[linha.tipo_voto] = {"votos": int(linha.qt_votos), "pct": float(linha.pct)}

    for linha in total.reset_index().itertuples():
        chave_cargo = "presidente" if linha.cargo == "PRESIDENTE" else "deputado_federal"
        no = resultados.setdefault(int(linha.id_regiao), {})
        no.setdefault("total_votos", {}).setdefault(chave_cargo, {}) \
          .setdefault(str(linha.ano_eleicao), {})[str(linha.turno)] = int(linha.total)

    return resultados


def publicar_regioes(dim: pd.DataFrame, resultados: dict[int, dict]) -> None:
    for cd_municipio, grupo in dim.groupby("cd_municipio_ibge"):
        payload = {
            "municipio": {"cd": cd_municipio, "nome": grupo.iloc[0]["nm_municipio"],
                          "uf": grupo.iloc[0]["sg_uf"]},
            "regioes": {},
        }
        for linha in grupo.itertuples():
            # Coluna de lista volta do parquet como array do numpy, cujo valor
            # verdade é ambíguo — a conversão precisa ser explícita.
            bruto = linha.outros_locais_mesma_coordenada
            outros = [str(x) for x in bruto] if bruto is not None and len(bruto) else []
            payload["regioes"][str(linha.id_regiao)] = {
                "local": linha.nm_local_votacao,
                "endereco": linha.ds_endereco,
                "lat": round(float(linha.latitude_final), 6),
                "lon": round(float(linha.longitude_final), 6),
                # Coordenada compartilhada nem sempre é o mesmo prédio: o site
                # mostra o representante e avisa que há outros locais no ponto.
                "outros_locais": outros,
                "resultados": resultados.get(int(linha.id_regiao), {}),
            }
        escrever_json(config.DIR_PUBLICADO / "regioes" / f"{cd_municipio}.json", payload)


def _lista_de_regioes(df: pd.DataFrame) -> list:
    return [[int(r), round(float(f), 3)] for r, f in df[["id_regiao", "fracao"]].values]


def publicar_ruas(trechos: pd.DataFrame, dominante: pd.DataFrame,
                  ruas_regioes: pd.DataFrame, ruas_bairro: pd.DataFrame) -> None:
    chave = ["cd_municipio_ibge", "logradouro"]
    trechos_por_rua = (
        trechos.sort_values("numero_inicial")
        .groupby(chave)[["numero_inicial", "id_regiao"]]
        .apply(lambda d: [[int(n), int(r)] for n, r in d.values])
        .rename("trechos").reset_index()
    )

    # Só as ruas ambíguas têm estas duas listas — ver passo 32.
    regioes_por_rua = (
        ruas_regioes.groupby(chave)[["id_regiao", "fracao"]]
        .apply(_lista_de_regioes).rename("regioes").reset_index()
    )
    por_bairro = (
        ruas_bairro.groupby(chave + ["bairro"])[["id_regiao", "fracao", "peso"]]
        .apply(lambda d: (_lista_de_regioes(d), float(d["peso"].sum())))
        .rename("conteudo").reset_index()
    )
    por_bairro["lista"], por_bairro["peso"] = zip(*por_bairro["conteudo"])
    bairros_por_rua = (
        por_bairro.sort_values("peso", ascending=False)
        .groupby(chave)[["bairro", "lista"]]
        .apply(lambda d: [[b, lst] for b, lst in d.values])
        .rename("bairros").reset_index()
    )

    completo = (
        dominante.merge(trechos_por_rua, on=chave, how="left")
        .merge(regioes_por_rua, on=chave, how="left")
        .merge(bairros_por_rua, on=chave, how="left")
    )

    for cd_municipio, grupo in completo.groupby("cd_municipio_ibge"):
        ruas = []
        for linha in grupo.sort_values("logradouro").itertuples():
            rua = {
                "nome": linha.logradouro,
                "regiao_provavel": int(linha.id_regiao_dominante),
                "n_regioes": int(linha.n_regioes),
                # Fração de endereços da rua na região provável: quando é 1.0, o
                # número da casa é dispensável; quando é baixa, o site precisa
                # insistir no número ou no bairro em vez de chutar.
                "confianca": round(float(linha.fracao), 3),
                "trechos": linha.trechos if isinstance(linha.trechos, list) else [],
            }
            # Rua de região única não leva lista nenhuma: a resposta é o próprio
            # `regiao_provavel`, e publicar a lista dobraria o arquivo à toa.
            if isinstance(linha.regioes, list):
                rua["regioes"] = linha.regioes
            if isinstance(linha.bairros, list):
                rua["bairros"] = linha.bairros
            ruas.append(rua)
        escrever_json(config.DIR_PUBLICADO / "ruas" / f"{cd_municipio}.json", {"ruas": ruas})


def publicar_bairros(bairros_regioes: pd.DataFrame) -> None:
    """Índice município × bairro — o caminho de quem não sabe o nome da rua."""
    por_bairro = (
        bairros_regioes.groupby(["cd_municipio_ibge", "bairro"])[["id_regiao", "fracao", "peso"]]
        .apply(lambda d: (_lista_de_regioes(d), float(d["peso"].sum())))
        .rename("conteudo").reset_index()
    )
    por_bairro["regioes"], por_bairro["peso"] = zip(*por_bairro["conteudo"])

    for cd_municipio, grupo in por_bairro.groupby("cd_municipio_ibge"):
        bairros = [
            {"nome": linha.bairro, "regioes": linha.regioes}
            for linha in grupo.sort_values("peso", ascending=False).itertuples()
        ]
        escrever_json(config.DIR_PUBLICADO / "bairros" / f"{cd_municipio}.json", {"bairros": bairros})


def publicar_indices(dim: pd.DataFrame) -> None:
    municipios_por_uf = (
        dim[["sg_uf", "cd_municipio_ibge", "nm_municipio"]].drop_duplicates()
        .sort_values(["sg_uf", "nm_municipio"])
    )
    ufs = [
        {"sigla": uf, "nome": NOMES_UF[uf], "n_municipios": int(len(g))}
        for uf, g in municipios_por_uf.groupby("sg_uf")
    ]
    escrever_json(config.DIR_PUBLICADO / "ufs.json", ufs)

    for uf, grupo in municipios_por_uf.groupby("sg_uf"):
        escrever_json(
            config.DIR_PUBLICADO / "municipios" / f"{uf}.json",
            [{"cd": linha.cd_municipio_ibge, "nome": linha.nm_municipio} for linha in grupo.itertuples()],
        )


def main() -> None:
    print("=== 40 · dados publicados ===")
    config.garantir_pastas()

    def ler(nome: str) -> pd.DataFrame:
        return pd.read_parquet(config.DIR_INTERMEDIARIO / f"{nome}.parquet")

    dim = ler("dim_regiao")
    votos = ler("votos_regiao")
    trechos = ler("indice_ruas")
    dominante = ler("ruas_dominante")
    ruas_regioes = ler("ruas_regioes")
    ruas_bairro = ler("ruas_bairro_regioes")
    bairros_regioes = ler("bairros_regioes")

    # Só publica região que tem resultado para mostrar.
    dim = dim[dim["id_regiao"].isin(set(votos["id_regiao"]))]
    print(f"  regiões publicadas: {len(dim):,}")

    resultados = calcular_resultados(votos)
    publicar_indices(dim)
    publicar_regioes(dim, resultados)
    publicar_ruas(trechos, dominante, ruas_regioes, ruas_bairro)
    publicar_bairros(bairros_regioes)

    # Parquet para uso analítico, além do JSON que o site consome.
    dim.to_parquet(config.DIR_PUBLICADO / "regioes.parquet", index=False)
    votos.to_parquet(config.DIR_PUBLICADO / "votos_regiao.parquet", index=False)
    trechos.to_parquet(config.DIR_PUBLICADO / "indice_ruas.parquet", index=False)

    total_bytes = sum(p.stat().st_size for p in config.DIR_PUBLICADO.rglob("*") if p.is_file())
    n_arquivos = sum(1 for p in config.DIR_PUBLICADO.rglob("*") if p.is_file())
    print(f"  arquivos: {n_arquivos:,} | tamanho: {total_bytes / 1e6:.1f} MB")
    print(f"  salvo em: {config.DIR_PUBLICADO}")


if __name__ == "__main__":
    main()

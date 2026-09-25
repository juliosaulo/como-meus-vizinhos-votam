"""Configuração única do pipeline — caminhos, anos, cargos e constantes.

Nenhum script do `pipeline/` define caminho por conta própria: tudo sai daqui,
para que trocar o recorte (UFs, ano de referência) seja uma edição
só, num lugar só.

Os dados brutos (TSE e CNEFE, ~8 GB) não vão para o repositório. Por padrão o
pipeline os procura em `dados/bruto/`. Quem já tiver esses arquivos em outro
lugar pode apontar para lá com a variável de ambiente `VOTO_REGIAO_BRUTO`, sem
copiar nada:

    # Windows (PowerShell)
    $env:VOTO_REGIAO_BRUTO = "C:\\caminho\\para\\os\\brutos"
    # Linux/macOS
    export VOTO_REGIAO_BRUTO=/caminho/para/os/brutos
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# =========================================================
# Caminhos
# =========================================================
DIR_BRUTO = Path(os.environ.get("VOTO_REGIAO_BRUTO", BASE_DIR / "dados" / "bruto"))
DIR_INTERMEDIARIO = BASE_DIR / "dados" / "intermediario"
DIR_IMPORTADO = BASE_DIR / "dados_importados"
DIR_PUBLICADO = BASE_DIR / "publicado"

# Subpastas esperadas dentro de DIR_BRUTO (mesma organização em que o TSE e o
# IBGE distribuem os arquivos — ver README, seção "Dados de origem").
DIR_BRUTO_CNEFE = DIR_BRUTO / "cnefe"                    # 27 zips, um por UF: 11_RO.zip ...
DIR_BRUTO_VOTACAO_PRESIDENTE = DIR_BRUTO / "votacao_presidente"  # votacao_secao_AAAA_BR.zip
DIR_BRUTO_VOTACAO_UF = DIR_BRUTO / "votacao_uf"          # votacao_secao_AAAA_UF.zip
DIR_BRUTO_CANDIDATOS = DIR_BRUTO / "candidatos"          # consulta_cand_AAAA.zip

# Artefato importado do projeto anterior (a geocodificação, que não se refaz —
# ver dados_importados/PROVENIENCIA.md).
ARQ_LOCAIS_GEOCODIFICADOS = DIR_IMPORTADO / "locais_votacao_2018_2022.parquet"

# =========================================================
# Recorte
# =========================================================
# UFs processadas. `None` = todas as 27. Para um teste rápido, uma lista curta (ex.: ["RO"]).
UFS_ALVO: list[str] | None = None

ANOS_ELEICAO = [2018, 2022]
CARGOS_ALVO = ["PRESIDENTE", "DEPUTADO FEDERAL"]

# Ano cujos locais de votação definem a malha de regiões — é "onde a pessoa vota
# hoje". Uma região é ativa se teve voto neste ano (passo 22); os votos dos
# outros anos são realocados para ela. Os locais saem do próprio arquivo de
# votação por seção, então 2026 só pode virar referência depois que o resultado
# for publicado. Antes de trocar, rode `qualidade/comparar_locais.py --ano 2026`.
ANO_REFERENCIA_MALHA = 2022

# Atribuição endereço → região (passo 31), com checkpoint por UF. Fica numa
# pasta por ano de referência: trocar a malha nunca reaproveita, por engano, a
# atribuição calculada com os locais de outro ano.
DIR_ENDERECOS_REGIAO = DIR_INTERMEDIARIO / "enderecos_regiao" / str(ANO_REFERENCIA_MALHA)

# Resumo, por região, da distância até os endereços que ela atende — calculado
# de graça no passo 31, porque a árvore já devolve essa distância.
DIR_DISTANCIAS_REGIAO = DIR_INTERMEDIARIO / "distancias_regiao" / str(ANO_REFERENCIA_MALHA)

N_TOP_DEPUTADOS = 3

# =========================================================
# Geografia
# =========================================================
# SIRGAS 2000 / Brasil Polyconic — projeção em METROS válida para o país
# inteiro. Toda distância é calculada aqui; calcular em graus (EPSG:4674)
# distorce a distância conforme a latitude e daria vizinho mais próximo errado.
CRS_METRICO = "EPSG:5880"
CRS_GEOGRAFICO = "EPSG:4674"

# =========================================================
# Códigos oficiais do TSE
# =========================================================
# Códigos de "candidato" que na verdade são totalizadores de voto não-nominal.
CODIGO_VOTO_BRANCO = "95"
CODIGO_VOTO_NULO = "96"
CODIGO_VOTO_ANULADO = "97"
CODIGO_VOTO_LEGENDA = "-3"
# Sequencial vazio (#NULO). Aparece no lugar de -3 no voto de legenda do DF
# em 2018 — ver `classificar_tipo_voto` no passo 21.
SQ_CANDIDATO_NULO = "-1"

# Só eleição ORDINÁRIA. Sem este filtro, eleição suplementar publicada sob o
# mesmo ano entra somada (aconteceu de verdade: MT/Senador 2018 e RR/Governador
# 2022) — ver `qualidade/validacoes.py`.
CD_TIPO_ELEICAO_ORDINARIA = "2"

# Duas grafias para voto em branco convivem no dado bruto do TSE (mudança de
# nomenclatura entre 2018 e 2022) — as duas precisam ser somadas juntas.
GRAFIAS_VOTO_BRANCO = ["VOTO BRANCO", "VOTO EM BRANCO"]

# =========================================================
# Resultado oficial, para validação (fonte: divulgação oficial do TSE)
# =========================================================
# O pipeline aborta se o total nacional apurado não bater exatamente com isto.
# Chave: ano → turno. Turno sem referência cadastrada é pulado com aviso.
TOTAIS_OFICIAIS_PRESIDENTE = {
    2018: {
        1: {"BOLSONARO": 49_277_010, "HADDAD": 31_342_051},
        2: {"BOLSONARO": 57_797_847, "HADDAD": 47_040_906},
    },
    2022: {
        1: {"LULA": 57_259_504, "BOLSONARO": 51_072_345},
        2: {"LULA": 60_345_999, "BOLSONARO": 58_206_354},
    },
}


def ufs_para_processar() -> list[str]:
    """Lista de UFs desta execução, em ordem estável."""
    return sorted(UFS_ALVO) if UFS_ALVO else sorted(SIGLAS_UF)


SIGLAS_UF = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
]


def garantir_pastas() -> None:
    """Cria as pastas de saída, se ainda não existirem."""
    for pasta in (DIR_INTERMEDIARIO, DIR_PUBLICADO):
        pasta.mkdir(parents=True, exist_ok=True)

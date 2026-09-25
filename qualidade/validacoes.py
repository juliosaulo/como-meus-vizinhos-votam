"""Guardas de qualidade do pipeline.

Cada função aqui existe por causa de um erro que **de fato aconteceu** na
construção da base original deste dado — não são checagens defensivas
genéricas. O nome de cada uma diz o que ela impede, e a docstring conta o
episódio, porque um projeto reprodutível precisa explicar não só o que faz,
mas o que já deu errado antes.

A regra de uso é: falhar alto e cedo. Uma base eleitoral silenciosamente
errada é pior que um pipeline que não roda — quem consome não tem como
perceber que o número está torto.
"""

from __future__ import annotations

import pandas as pd


class ValidacaoFalhou(AssertionError):
    """Erro de validação do pipeline — sempre aborta a execução."""


# =========================================================
# 1. Código de município com 5 dígitos
# =========================================================

def normalizar_codigo_municipio(valores: pd.Series) -> pd.Series:
    """Código de município do TSE sempre como string de 5 dígitos.

    O TSE exporta `CD_MUNICIPIO` sem zero à esquerda em alguns arquivos (2018,
    notadamente) e com 5 dígitos fixos em outros. Como o identificador do local
    de votação é a concatenação UF + município + zona + local, isso fazia o
    MESMO prédio virar dois registros distintos conforme o ano do arquivo.

    Na base original o estrago foi de 13.913 locais duplicados (18,9% da base),
    contaminando 26,4% da base de treino do classificador de geocodificação e
    39,1% de uma fila de 21.890 casos que já tinham sido rotulados à mão. A
    correção é trivial; o custo de não ter feito desde o início não foi.
    """
    return valores.astype(str).str.strip().str.zfill(5)


def checar_codigo_municipio(valores: pd.Series, contexto: str) -> None:
    larguras = valores.astype(str).str.len().unique()
    if set(larguras) != {5}:
        raise ValidacaoFalhou(
            f"{contexto}: código de município deveria ter sempre 5 dígitos, "
            f"encontrei larguras {sorted(larguras)}. Use normalizar_codigo_municipio()."
        )


# =========================================================
# 2. Turno é string
# =========================================================

def normalizar_turno(valores: pd.Series) -> pd.Series:
    """`turno` como string `"1"`/`"2"`.

    Comparar com inteiro (`df["turno"] == 2`) nunca casa nada e devolve
    silenciosamente um DataFrame vazio — nenhum erro é levantado, o resultado
    simplesmente vira zero.
    """
    return valores.astype(str).str.strip()


# =========================================================
# 3. Só eleição ordinária
# =========================================================

def filtrar_eleicao_ordinaria(df: pd.DataFrame, coluna: str = "CD_TIPO_ELEICAO") -> pd.DataFrame:
    """Descarta eleição suplementar/extraordinária empilhada sob o mesmo ano.

    O arquivo bruto do TSE traz, sob o mesmo `ANO_ELEICAO`, eleições
    suplementares realizadas depois (mandato cassado → nova eleição). Somar as
    duas como se fossem uma inflava o total: aconteceu em Mato Grosso
    (Senador, 2018) e Roraima (Governador, 2022), e só apareceu porque a
    validação por seção acusou divergência.
    """
    from config import CD_TIPO_ELEICAO_ORDINARIA

    if coluna not in df.columns:
        raise ValidacaoFalhou(
            f"coluna {coluna!r} ausente — sem ela não dá para separar eleição "
            "ordinária de suplementar, e o total sai inflado."
        )
    antes = len(df)
    df = df[df[coluna].astype(str).str.strip() == CD_TIPO_ELEICAO_ORDINARIA]
    descartadas = antes - len(df)
    if descartadas:
        print(f"    [ordinária] {descartadas:,} linha(s) de eleição suplementar descartadas")
    return df


# =========================================================
# 4. Turnos nunca somados entre si
# =========================================================

def checar_turno_unico(df: pd.DataFrame, contexto: str, coluna: str = "turno") -> None:
    """Garante que só há um turno no recorte antes de somar votos.

    Presidente teve 2º turno em 2018 e 2022. Somar 1º e 2º conta o mesmo
    eleitor duas vezes — um erro que não estoura em lugar nenhum, só produz
    percentuais errados.
    """
    turnos = sorted(df[coluna].astype(str).unique())
    if len(turnos) > 1:
        raise ValidacaoFalhou(
            f"{contexto}: o recorte tem mais de um turno ({turnos}) e está prestes a "
            "somar votos. Filtre o turno antes."
        )


# =========================================================
# 5. Duas grafias de voto em branco
# =========================================================

def unificar_voto_branco(nomes: pd.Series) -> pd.Series:
    """Colapsa "VOTO BRANCO" e "VOTO EM BRANCO" numa grafia só.

    As duas convivem no dado bruto (provável mudança de nomenclatura do TSE
    entre 2018 e 2022). Agrupar por `nm_candidato` sem unificar parte o voto em
    branco em duas linhas que ninguém soma depois.
    """
    from config import GRAFIAS_VOTO_BRANCO

    return nomes.replace({g: "VOTO EM BRANCO" for g in GRAFIAS_VOTO_BRANCO})


# =========================================================
# 6. Chave composta do CNEFE
# =========================================================

def checar_chave_unica(df: pd.DataFrame, colunas: list[str], contexto: str) -> None:
    """Falha se as colunas não formam chave única.

    Usada sobretudo para `(id_cnefe, cod_especie)`: o código de endereço do
    CNEFE **não** é único sozinho — o mesmo código serve um domicílio e um
    estabelecimento no mesmo lote. Um merge só por `id_cnefe` faz produto
    cartesiano e infla a contagem de endereços sem avisar.
    """
    duplicadas = int(df.duplicated(subset=colunas).sum())
    if duplicadas:
        raise ValidacaoFalhou(
            f"{contexto}: {duplicadas:,} linha(s) duplicadas em {colunas} — "
            "essas colunas não formam chave única, o merge vai inflar."
        )


# =========================================================
# 7. Voto soma, atributo de local deduplica
# =========================================================

def checar_soma_preservada(
    antes: pd.Series | float, depois: pd.Series | float, contexto: str, tolerancia: int = 0
) -> None:
    """Confere que uma agregação não perdeu nem inventou voto.

    Vários locais de votação (zonas/seções diferentes) podem dividir o mesmo
    prédio e a mesma coordenada. Ao consolidar, voto tem que ser SOMADO (são
    urnas distintas, eleitores distintos) enquanto atributo do local tem que ser
    DEDUPLICADO (é o mesmo lugar). Trocar os dois é o erro clássico aqui: soma
    de atributo infla população, dedup de voto some com eleitor.
    """
    total_antes = int(pd.Series(antes).sum()) if hasattr(antes, "__len__") else int(antes)
    total_depois = int(pd.Series(depois).sum()) if hasattr(depois, "__len__") else int(depois)
    diferenca = abs(total_antes - total_depois)
    if diferenca > tolerancia:
        raise ValidacaoFalhou(
            f"{contexto}: soma mudou na agregação — antes {total_antes:,}, "
            f"depois {total_depois:,} (diferença de {diferenca:,})."
        )


def checar_constante_por_grupo(
    df: pd.DataFrame, chave: list[str], colunas: list[str], contexto: str
) -> None:
    """Confere que `colunas` têm valor único dentro de cada grupo de `chave`.

    Usada ao deduplicar locais por coordenada: se município ou UF variassem
    dentro de um mesmo par de coordenadas, o agrupamento seria ambíguo e a
    região herdaria um município arbitrário.
    """
    contagem = df.groupby(chave, observed=True)[colunas].nunique()
    problemas = contagem[(contagem > 1).any(axis=1)]
    if len(problemas):
        raise ValidacaoFalhou(
            f"{contexto}: {len(problemas):,} grupo(s) de {chave} têm mais de um valor "
            f"em {colunas} — o agrupamento seria ambíguo. Exemplos:\n{problemas.head()}"
        )


def checar_taxa_de_juncao(
    casados: int, total: int, contexto: str, minimo: float = 0.5
) -> None:
    """Falha quando um merge casa menos linhas do que deveria.

    Um `merge` que não encontra contrapartida não levanta erro: devolve menos
    linhas, ou nenhuma, e o pipeline segue produzindo uma base vazia. Aconteceu
    aqui na primeira execução — o identificador do local de votação foi montado
    sem os separadores que a base de referência usa (`RO_00019_1_1031`), e o
    resultado foi uma junção de 0%, salva em disco sem uma única reclamação.

    Uma taxa baixa quase sempre significa formato de chave divergente, não
    ausência real de dado.
    """
    taxa = casados / total if total else 0.0
    print(f"    [junção] {contexto}: {casados:,}/{total:,} ({taxa * 100:.1f}%)")
    if taxa < minimo:
        raise ValidacaoFalhou(
            f"{contexto}: só {taxa * 100:.1f}% das linhas encontraram contrapartida "
            f"(mínimo esperado: {minimo * 100:.0f}%). Quase sempre é formato de chave "
            "divergente entre as duas bases — confira um exemplo de cada lado."
        )


# =========================================================
# 8. Distância sempre em metros
# =========================================================

def checar_crs_metrico(crs, contexto: str) -> None:
    """Falha se o cálculo de distância for feito em graus.

    Vizinho mais próximo calculado em latitude/longitude usa "graus" como
    unidade, e um grau de longitude vale ~111 km no equador e ~78 km no sul do
    país. O vizinho mais próximo em graus não é o vizinho mais próximo em
    metros — o erro é silencioso e enviesado por latitude.
    """
    from config import CRS_METRICO

    if str(crs) != CRS_METRICO:
        raise ValidacaoFalhou(
            f"{contexto}: distância precisa ser calculada em {CRS_METRICO} (metros), "
            f"recebi {crs}."
        )


def checar_coordenadas_no_brasil(lat: pd.Series, lon: pd.Series, contexto: str) -> None:
    """Confere que lat/lon caem na caixa do território brasileiro.

    Pega troca de latitude com longitude, que é um erro fácil de cometer e
    difícil de ver: as coordenadas continuam "válidas", só apontam para o
    oceano Índico.
    """
    fora = (
        (lat < -34.0) | (lat > 5.3) | (lon < -74.0) | (lon > -34.7)
    ) & lat.notna() & lon.notna()
    if fora.any():
        raise ValidacaoFalhou(
            f"{contexto}: {int(fora.sum()):,} coordenada(s) fora do território brasileiro "
            "— provável troca de latitude com longitude."
        )


# =========================================================
# 9. Total nacional bate com o resultado oficial
# =========================================================

def validar_totais_oficiais(votos_por_candidato: dict[str, int], ano: int, turno: str | int) -> None:
    """Confere o total nacional apurado contra o resultado oficial divulgado.

    É a validação mais importante do pipeline: qualquer erro de leitura,
    filtro, junção ou agregação que afete a contagem aparece aqui. Se não
    bater, o pipeline aborta em vez de publicar número errado.
    """
    from config import TOTAIS_OFICIAIS_PRESIDENTE

    esperado = TOTAIS_OFICIAIS_PRESIDENTE.get(ano, {}).get(int(turno))
    if esperado is None:
        print(
            f"    [AVISO oficial] sem referência oficial cadastrada para {ano}, {turno}º turno "
            "— checagem pulada. Cadastre em config.TOTAIS_OFICIAIS_PRESIDENTE."
        )
        return

    erros = []
    for nome, total_oficial in esperado.items():
        apurado = next(
            (v for k, v in votos_por_candidato.items() if nome in k.upper()), None
        )
        if apurado is None:
            erros.append(f"  {nome}: não encontrado no apurado")
        elif apurado != total_oficial:
            erros.append(
                f"  {nome}: apurado {apurado:,} × oficial {total_oficial:,} "
                f"(diferença de {apurado - total_oficial:+,})"
            )
    if erros:
        raise ValidacaoFalhou(
            f"total nacional de Presidente ({ano}, {turno}º turno) não bate com o resultado "
            "oficial:\n" + "\n".join(erros)
        )
    print(f"    [oficial] totais de {ano} ({turno}º turno) batem exatamente com o resultado divulgado")

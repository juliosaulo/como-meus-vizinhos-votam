# Pipeline, passo a passo

Este documento percorre o pipeline na ordem em que ele roda. Para cada passo: o que entra, o que
acontece, o que sai, quais guardas de qualidade são executadas e os números reais da execução
nacional (27 UFs).

O **porquê** das decisões de método (vizinho mais próximo no município, índice por trechos,
realocação do histórico) está em [METODOLOGIA.md](METODOLOGIA.md). O que o dado não cobre está em
[LIMITACOES.md](LIMITACOES.md). Aqui fica o **como**.

## Visão geral

| Passo | Script | Entrada | Saída |
|---|---|---|---|
| — | *artefato importado* | — | `dados_importados/locais_votacao_2018_2022.parquet` |
| 10 | `pipeline/10_locais_oficiais.py` | base oficial de locais (TSE) | `locais_oficiais`, `eleitorado_local` |
| 11 | `pipeline/11_montar_dim_regiao.py` | locais geocodificados + passo 10 | `dim_regiao`, `de_para_local_regiao` |
| 21 | `pipeline/21_processar_votacao.py` | votação por seção + cadastro de candidatos (TSE) | `votos_local_votacao` |
| 22 | `pipeline/22_votos_por_regiao.py` | passos 11 e 21 | `votos_regiao` |
| 31 | `pipeline/31_atribuir_endereco_regiao.py` | CNEFE + passos 11 e 22 | `enderecos_regiao/{ano}/{UF}` |
| 32 | `pipeline/32_montar_indice_ruas.py` | passo 31 | `indice_ruas`, `ruas_dominante`, `ruas_regioes`, `ruas_bairro_regioes`, `bairros_regioes` |
| 40 | `pipeline/40_build_dados_publicados.py` | passos 10, 11, 22 e 32 | tudo em `publicado/` |
| pub | `qualidade/validar_publicado.py` | `publicado/` | conferência dos arquivos (não gera saída) |
| qa | `qualidade/validar_indice_ruas.py` | `publicado/` + CNEFE | `publicado/validacao_indice_ruas.json` |
| cob | `qualidade/relatorio_cobertura.py` | todos os anteriores | `publicado/cobertura.json` |
| dist | `qualidade/diagnostico_distancias.py` | passo 31 | `publicado/diagnostico_distancias.json` |

Arquivos intermediários ficam em `dados/intermediario/` (fora do git); os finais em `publicado/`
(versionados). A numeração tem lacunas de propósito — os passos são agrupados por dezena:
1x malha de regiões, 2x votos, 3x endereços, 4x publicação.

```
     dados_importados/locais_votacao_2018_2022   TSE (locais oficiais)
                                 │                        │
                                 │                       10
                                 │                        │
                                 │        locais_oficiais, eleitorado_local
                                 │                        │
                                 └───────────┬────────────┘
                                             ▼
                         ┌──── 11 ────┐
                         │            │
                  dim_regiao    de_para_local_regiao
                   │    │             │
   TSE ──► 21 ──► votos_local_votacao │
                   │    │             │
                   │    └──► 22 ◄─────┘
                   │          │
                   │     votos_regiao
                   │      │   │
  CNEFE ───────────┼──► 31 ◄──┘
                   │      │
                   │  enderecos_regiao/{ano}/{UF}
                   │      │
                   │      ▼
                   │     32 ──► índice de trechos, listas por rua e por bairro
                   │      │
                   └────► 40 ◄── (votos_regiao)
                          │
                      publicado/ ──► qa ──► cob
```

## Rodando

```bash
python rodar_pipeline.py              # todos os passos, na ordem
python rodar_pipeline.py --de 31      # a partir de um passo
python rodar_pipeline.py --so 21 22   # só alguns
python rodar_pipeline.py --listar
```

Cada passo lê do disco o que o anterior gravou, então interromper e retomar é seguro. O Brasil
inteiro roda do zero em **cerca de uma hora**, numa máquina com 16 GB de RAM; o passo 31 é o mais
pesado. Os passos que juntam o país inteiro (32, validação e cobertura) processam uma UF por vez
para caber na memória. Só Rondônia (`UFS_ALVO = ["RO"]`) roda em cerca de um minuto e meio.

O recorte é definido em [`config.py`](config.py): `UFS_ALVO` (padrão `None` = as 27),
`ANOS_ELEICAO`, `CARGOS_ALVO` e `ANO_REFERENCIA_MALHA`.

---

## Antes de começar: os dados de origem

O pipeline espera os arquivos brutos em `dados/bruto/`, organizados como o TSE e o IBGE os
distribuem. Quem já os tiver em outro lugar aponta com a variável `VOTO_REGIAO_BRUTO`.

| Pasta | Conteúdo | Fonte |
|---|---|---|
| `votacao_presidente/` | `votacao_secao_2018_BR.zip`, `votacao_secao_2022_BR.zip` | TSE — votação por seção, eleição geral federal |
| `votacao_uf/2018/`, `votacao_uf/2022/` | `votacao_secao_AAAA_UF.zip`, um por estado | TSE — votação por seção, eleições gerais estaduais |
| `candidatos/2018/`, `candidatos/2022/` | `consulta_cand_AAAA.zip` | TSE — cadastro de candidatos |
| `cnefe/` | `11_RO.zip` … `53_DF.zip` | IBGE — CNEFE do Censo 2022 |

Presidente e os demais cargos vêm em arquivos separados porque, no TSE, são eleições registradas à
parte, mesmo ocorrendo no mesmo dia e na mesma urna.

---

## Artefato importado: os locais de votação geocodificados

**Arquivo:** `dados_importados/locais_votacao_2018_2022.parquet`

É o único dado que o pipeline não produz. O TSE informa nome e endereço de cada local de votação,
mas não a coordenada; ela foi obtida num trabalho anterior, pareando esse texto com o CNEFE por meio
de um classificador supervisionado e de mais de 24 mil decisões manuais. O método completo está em
[PROVENIENCIA.md](dados_importados/PROVENIENCIA.md).

| | |
|---|---|
| Locais de votação (2018 e/ou 2022) | 93.658 |
| Com coordenada | 78.568 |
| Abrangência | 5.570 municípios, 27 UFs + `ZZ` (exterior) |

Colunas: `id_local_votacao`, `sg_uf`, `nm_municipio`, `cd_municipio_ibge`,
`nm_local_votacao_consolidado`, `ds_local_votacao_endereco_consolidado`, `status_geocodificacao`,
`latitude_final`, `longitude_final`.

O identificador tem o formato `RO_00019_1_1031`: UF, código TSE do município com 5 dígitos, zona e
número do local, separados por sublinhado.

---

## Passo 10 — Locais oficiais do TSE

**Script:** `pipeline/10_locais_oficiais.py`
**Entrada:** `dados/bruto/locais_oficiais/eleitorado_local_votacao_AAAA.zip` (2018, 2022, 2026)
**Saídas:** `dados/intermediario/locais_oficiais.parquet`, `dados/intermediario/eleitorado_local.parquet`

O TSE publica, para cada eleição, uma linha por seção eleitoral com o prédio onde ela funciona:
nome, endereço, coordenada e eleitorado. Este passo transforma isso em duas tabelas — uma de locais
(um registro por local, com a descrição da eleição mais recente em que ele aparece) e uma de
eleitorado por local, ano e turno.

A coordenada oficial **não substitui** a do artefato geocodificado; ela entra onde o artefato não
chega. O porquê, com os números que sustentam a decisão, está em
[PROVENIENCIA.md](dados_importados/PROVENIENCIA.md).

### Cuidados do formato

- **Coordenada ausente não vem vazia.** O TSE publica `0` ou o sentinela `-1` — quase todo o arquivo
  de 2018 — e ainda sobram valores impossíveis. Tudo que cai fora da caixa do Brasil
  (`validacoes.CAIXA_BRASIL_LAT/LON`) é descartado como ausente, em vez de virar um local no oceano.
- **Separador decimal muda com o ano**: vírgula em 2026, ponto em 2022.
- **O mesmo conteúdo vem em dois formatos**: um CSV nacional (2018, 2022) ou um por UF mais um
  `BRASIL` que repete tudo (2026). Ler os dois contaria cada seção duas vezes.
- **Eleitorado é por seção** e precisa ser somado por local; a guarda de conservação confere que
  nenhuma seção se perdeu nem foi contada duas vezes.

### Brasil

| | |
|---|---|
| seções lidas (2018, 2022 e 2026) | 2.463.568 |
| locais distintos | 107.190 |
| locais com coordenada aproveitável | 102.483 |
| eleitorado 2022, 1º turno | 155.758.656 |
| eleitorado 2026, 1º turno | 157.828.968 |

## Passo 11 — Dimensão de regiões

**Script:** `pipeline/11_montar_dim_regiao.py`
**Entrada:** o artefato importado + o passo 10
**Saídas:** `dados/intermediario/dim_regiao.parquet`, `dados/intermediario/de_para_local_regiao.parquet`

Define o que é uma **região**: o conjunto de locais de votação que ocupam a mesma coordenada. Vários
locais (zonas e seções diferentes) podem funcionar no mesmo prédio, e por isso a unidade é o ponto,
não o local.

Vale ser explícito sobre o nome, porque ele engana. Uma região **é um ponto de votação** — uma
coordenada, com os locais que funcionam nela. Não é uma área, e nenhum passo deste pipeline desenha
ou publica uma. A palavra "região" descreve o uso que se faz desse ponto: a área que ele atende é o
conjunto de endereços que o têm como o mais próximo dentro do município, e isso é calculado
endereço a endereço no passo 31, sem fronteira nenhuma no meio. Por isso o site marca um pino no
mapa, e não uma mancha.

### O que acontece

1. **Completa a malha com a base oficial do passo 10**, nesta prioridade: a coordenada geocodificada
   manda onde existe; a oficial entra nos locais sem coordenada e nos que só aparecem em eleições
   mais novas. Local novo a menos de 50 m de um ponto já conhecido herda a coordenada dele — é a
   mesma escola com outra zona, e dois pontos a vinte metros virariam dois lugares no site.
   Coordenada oficial que cairia em dois municípios é descartada, porque tornaria ambíguo o
   agrupamento por ponto. Cada local fica com a `origem_coordenada` (`cnefe` ou `tse`).
2. Descarta `sg_uf == "ZZ"` (voto no exterior, sem correspondência no CNEFE).
3. Descarta locais sem coordenada — depois do complemento, são 0,3%.
4. Aplica o recorte de UFs de `config.UFS_ALVO`.
5. Confere que as coordenadas caem dentro do território brasileiro.
6. Confere que município e UF são constantes dentro de cada coordenada.
7. Agrupa por `(latitude_final, longitude_final)` exatas e numera as regiões em sequência, ordenadas
   por UF, município e coordenada.
8. Escolhe o **local representativo** da região — o primeiro em ordem alfabética de nome, depois por
   identificador — e guarda os nomes dos demais em `outros_locais_mesma_coordenada`.
9. Gera o de-para de cada local de votação para a sua região.

### Saídas

**`dim_regiao`** — uma linha por região.
`id_regiao`, `latitude_final`, `longitude_final`, `sg_uf`, `cd_municipio_ibge`, `nm_municipio`,
`n_locais_votacao`, `nm_local_votacao`, `ds_endereco`, `outros_locais_mesma_coordenada`

**`de_para_local_regiao`** — uma linha por local de votação.
`id_local_votacao`, `id_regiao`

### Guardas

| Guarda | Impede |
|---|---|
| `checar_coordenadas_no_brasil` | latitude trocada com longitude |
| `checar_constante_por_grupo` | região herdando um município arbitrário |
| `checar_chave_unica` no de-para | um local contado em duas regiões |

### Brasil

| | |
|---|---|
| Locais com coordenada | 78.568 |
| Regiões | **74.552** |
| Regiões com mais de um local na mesma coordenada | 3.120 (4,2%) |
| Municípios | 5.563 |

---

## Passo 21 — Votação por local de votação

**Script:** `pipeline/21_processar_votacao.py`
**Entrada:** votação por seção e cadastro de candidatos, para cada ano e cargo de `config`
**Saída:** `dados/intermediario/votos_local_votacao.parquet`

Transforma o arquivo bruto do TSE — um registro por seção e por votável, sem partido — numa tabela
de votos por local de votação, candidato e partido.

### O que acontece

Para cada combinação de ano (2018, 2022) e cargo (Presidente, Deputado Federal):

1. **Escolhe os arquivos.** Presidente: o arquivo nacional único. Deputado Federal: um arquivo por UF,
   só as do recorte.
2. **Lê em blocos de 500 mil linhas**, só as colunas necessárias.
3. **Filtra o cargo** comparando o nome sem acento.
4. **Descarta eleição suplementar** (`CD_TIPO_ELEICAO != "2"`).
5. **Soma o total nacional por votável e turno**, antes de qualquer recorte de UF.
6. **Aplica o recorte de UFs.**
7. **Normaliza o código do município** para 5 dígitos.
8. **Monta `id_local_votacao`** com separadores: `UF_MUNICIPIO_ZONA_LOCAL`.
9. **Classifica o tipo de voto** pelos códigos oficiais: `95` branco, `96` nulo, `97` anulado,
   `sq_candidato == -3` legenda (ou `-1` com número de 2 dígitos, como vem no DF em 2018), o resto
   nominal.
10. **Unifica as duas grafias de voto em branco.**
11. **Agrega** por local, turno, votável e tipo de voto, e concatena os blocos.
12. **Junta o partido** pelo sequencial do candidato, a partir do cadastro. Voto de legenda não tem
    candidato: o partido sai do número do votável.
13. **Valida o total nacional** de Presidente, turno a turno, contra o resultado oficial (passo 5).

### Saída

**`votos_local_votacao`** — uma linha por local × ano × turno × cargo × votável.
`id_local_votacao`, `sg_uf`, `ano_eleicao`, `turno`, `cargo`, `sq_candidato`, `nr_votavel`,
`nm_votavel`, `sg_partido`, `tipo_voto`, `qt_votos`

`tipo_voto` assume `nominal`, `legenda`, `branco`, `nulo` ou `anulado`.

### Guardas

| Guarda | Impede |
|---|---|
| `filtrar_eleicao_ordinaria` | eleição suplementar somada à ordinária |
| `normalizar_codigo_municipio` + `checar_codigo_municipio` | o mesmo prédio virar dois locais de votação |
| `unificar_voto_branco` | voto em branco partido em duas linhas |
| `normalizar_turno` | comparação com inteiro que nunca casa |
| legenda com sequencial `-1` em `classificar_tipo_voto` | voto de legenda contado como nominal sem partido (DF, 2018: 86.806 votos) |
| voto nominal sem partido → falha | cruzamento incompleto com o cadastro |
| `validar_totais_oficiais` | qualquer erro que altere a contagem |

A última é a mais forte. Como o arquivo de Presidente é nacional e o total é somado antes do recorte
de UF, a comparação com o resultado oficial roda **mesmo quando a execução é de um estado só**:

| Ano | Turno | Candidato | Oficial |
|---|---|---|---:|
| 2018 | 1º | Bolsonaro | 49.277.010 |
| 2018 | 1º | Haddad | 31.342.051 |
| 2018 | 2º | Bolsonaro | 57.797.847 |
| 2018 | 2º | Haddad | 47.040.906 |
| 2022 | 1º | Lula | 57.259.504 |
| 2022 | 1º | Bolsonaro | 51.072.345 |
| 2022 | 2º | Lula | 60.345.999 |
| 2022 | 2º | Bolsonaro | 58.206.354 |

Um voto de diferença aborta o pipeline. Turno sem total cadastrado em
`config.TOTAIS_OFICIAIS_PRESIDENTE` é pulado com aviso.

### Brasil

| Cargo | Ano | Linhas brutas lidas | Linhas agregadas | Votos |
|---|---|---:|---:|---:|
| Presidente | 2018 | 6.616.660 | 5.099.210 | 232.894.173 |
| Deputado Federal | 2018 | 67.013.525 | 19.712.073 | 117.111.570 |
| Presidente | 2022 | 5.380.736 | 3.559.519 | 247.320.988 |
| Deputado Federal | 2022 | 67.597.993 | 21.490.745 | 123.195.571 |
| **Total** | | | **49.861.547** | **720.522.302** |

Os votos somam os dois turnos de Presidente, por isso não se leem como total de eleitores. Os
totais oficiais batem exatamente nos dois turnos de 2018 e de 2022.

---

## Passo 22 — Votos por região

**Script:** `pipeline/22_votos_por_regiao.py`
**Entradas:** `votos_local_votacao` (passo 21), `de_para_local_regiao` e `dim_regiao` (passo 11)
**Saída:** `dados/intermediario/votos_regiao.parquet`

Leva os votos do local de votação para a região, na malha do ano de referência.

### O que acontece

1. **Junta votos e regiões** pelo `id_local_votacao`. Votos em local sem coordenada ficam de fora.
2. **Identifica as regiões ativas** no ano de referência (`config.ANO_REFERENCIA_MALHA`, hoje 2022).
   Ativa é a região que **existe** naquele ano: tem local de votação em funcionamento, segundo o
   eleitorado por seção do passo 10, **ou** tem voto apurado. A união das duas definições está em
   [`pipeline/malha.py`](pipeline/malha.py), e o motivo é a véspera de uma eleição — ver a caixa
   abaixo.
3. **Projeta as coordenadas** para EPSG:5880 (metros).
4. **Realoca as regiões inativas.** Município a município, cada região sem voto no ano de referência
   aponta para a região ativa mais próxima, por árvore de vizinho mais próximo. Região ativa aponta
   para si mesma. Município sem nenhuma região ativa mantém as próprias regiões.
5. **Substitui o `id_regiao`** de cada voto pelo da região de destino e **soma**.

### Por que "ativa" não é "tem voto"

As duas definições dão o mesmo conjunto depois de uma eleição: todo local que funcionou tem voto.
Elas divergem na véspera, e é aí que importa.

O TSE publica o eleitorado por seção **meses antes** da votação — em 2026, 95.116 locais com nome,
endereço e coordenada. Então a malha de 2026 pode estar pronta antes do resultado, e é isso que
permite trocar `ANO_REFERENCIA_MALHA` com antecedência, rodar as duas horas do passo 31 em
tranquilidade e deixar o dia da apuração só com o voto.

Se "ativa" fosse só "tem voto", a troca antecipada não funcionaria: zero regiões ativas, o
fallback do item 4 manteria as regiões antigas, e os endereços nunca seriam atribuídos às regiões
novas. Medido com a malha de 2026 antes de qualquer voto:

| | |
|---|---:|
| regiões ativas em 2026 | **90.898** |
| regiões realocadas | 7.083 |
| regiões com resultado depois da realocação | 85.586 |
| regiões publicadas **sem** resultado anterior | 5.312 |
| votos preservados | 717.237.591 → 717.237.591 |

As 5.312 são locais novos, que só passam a ter resultado quando o voto de 2026 chega. O passo 40
as publica mesmo assim (ver lá), porque o índice de ruas do passo 32 já aponta endereços para
elas — se não fossem publicadas, o site resolveria um endereço para uma região ausente do JSON.

### Saída

**`votos_regiao`** — uma linha por região × ano × turno × cargo × votável. Mesmas colunas de
`votos_local_votacao`, com `id_regiao` no lugar de `id_local_votacao`.

Apesar do nome, esta tabela é **voto por ponto de votação**, não por área. Ela difere de
`votos_local_votacao` em duas coisas: locais que dividem a mesma coordenada foram somados numa linha
só (3.120 pontos, 4,2% das regiões), e os votos de locais que não existiam no ano de referência
foram realocados para o ponto ativo mais próximo do município. A área só aparece no passo 31, e como
atribuição de cada endereço ao ponto mais próximo — nunca como polígono.

### Guardas

| Guarda | Impede |
|---|---|
| `checar_taxa_de_juncao` (mínimo 50%) | junção malformada que casa zero linhas e grava base vazia |
| `checar_soma_preservada` | realocação que perde ou inventa voto |
| `checar_chave_unica` | linha duplicada após a soma |

A primeira guarda é a mais importante deste passo. Um `merge` que não encontra contrapartida não
levanta exceção: devolve menos linhas, ou nenhuma, e o passo seguiria gravando uma base vazia. Como
a cobertura esperada aqui é a da malha completa (~99,5%), uma taxa perto de zero indica formato de
chave divergente entre as duas bases, não ausência real de dado.

### Brasil

| | |
|---|---|
| Linhas de voto que encontraram região | 49.616.732 de 49.861.547 (99,5%) |
| Votos em local geocodificado | 717.237.591 de 720.522.302 (**99,5%**) |
| Votos fora da malha | 3.284.711 |
| Regiões ativas em 2026 (malha de referência) | 90.898 |
| Regiões realocadas | 7.083 |
| Regiões com voto depois da realocação | 85.586 |
| Linhas de saída | 19.445.200 |

**Os 3,3 milhões de votos fora da malha não são 3,3 milhões de eleitores.** O total soma seis
recortes — Presidente e Deputado Federal, em 2018 e 2022, com os dois turnos de Presidente —, então
o mesmo eleitor é contado até seis vezes. Numa eleição só: no 2º turno de 2022 foram 86.661 votos
fora, de 123.942.648 apurados (0,1%).

São votos dados em local de votação sem coordenada, que por isso não pertence a região nenhuma:

| Situação do local | Votos | % | Locais |
|---|---:|---:|---:|
| Com coordenada — entram na malha | 717.237.591 | 99,5% | 95.522 |
| No cadastro importado, sem coordenada | 517.272 | 0,1% | 248 |
| Fora do cadastro importado | 2.767.439 | 0,4% | 1.881 |

A perda da geocodificação, que era a maior delas, é praticamente zerada pelo complemento com a
base oficial do TSE (passos 10 e 11). O que sobra são locais que aparecem na votação de 2018 ou
2022 e não estão em nenhuma das duas fontes — provável mudança de zona eleitoral entre o cadastro
e a apuração.

---

## Passo 31 — Endereço → região

**Script:** `pipeline/31_atribuir_endereco_regiao.py`
**Entradas:** CNEFE da UF, `dim_regiao` (passo 11), `votos_regiao` (passo 22)
**Saídas:** `dados/intermediario/enderecos_regiao/{ANO_REFERENCIA_MALHA}/{UF}.parquet` e
`dados/intermediario/distancias_regiao/{ANO_REFERENCIA_MALHA}/{UF}.parquet`

A pasta leva o ano da malha para que o checkpoint nunca reaproveite uma atribuição feita com os
locais de outro ano.

A segunda saída é o resumo, por região, da distância até os endereços que ela atende. A árvore de
vizinho mais próximo já calcula essa distância para responder a consulta; guardá-la custa quase
nada e alimenta o diagnóstico do fim deste documento.

Atribui cada endereço do CNEFE ao local de votação mais próximo dentro do mesmo município. É o passo
que responde à pergunta do site, e o mais pesado do pipeline.

### O que acontece

1. **Define as regiões-alvo**: as que têm voto em `votos_regiao` **mais** as da malha do ano de
   referência (`pipeline/malha.py`) — a união, para que local novo sem resultado também receba
   endereços, e para que região que recebe voto realocado não fique de fora. Projeta
   as coordenadas para EPSG:5880.
2. **Constrói uma árvore de vizinho mais próximo por município** (`scipy.cKDTree`).
3. Para cada UF:
   1. Pula a UF se já houver saída dela, depois de conferir que o checkpoint foi feito com a malha
      atual (mesmas colunas e regiões nos mesmos municípios).
   2. Lê o CNEFE em blocos de 400 mil linhas.
   3. Remove duplicatas pela chave `(COD_UNICO_ENDERECO, COD_ESPECIE)`.
   4. Descarta endereços sem coordenada.
   5. Monta o nome da rua juntando tipo, título e nome do logradouro, em maiúsculas.
   6. Converte o número para numérico; valor não numérico fica vazio.
   7. Guarda o bairro (`DSC_LOCALIDADE`) em maiúsculas e marca quais endereços são domicílio
      (`COD_ESPECIE` 1 e 2).
   8. Projeta os endereços para EPSG:5880.
   9. Consulta, município a município, a região mais próxima de cada endereço.
   10. **Conta** endereços e domicílios por município × rua × bairro × número × região.

A contagem do último passo é intencional, e não uma deduplicação. Quando o mesmo número de rua cai
em duas regiões — lados opostos da via, condomínio na divisa —, o passo 32 precisa saber qual delas
concentra mais domicílios para desempatar. Sem esse peso, o desempate viraria sorteio: medido no
piloto de Rondônia, isso custaria quase 5 pontos percentuais de acerto ponta a ponta.

### Saída

**`enderecos_regiao/{UF}`** — uma linha por município × rua × bairro × número × região.
`cd_municipio_ibge`, `logradouro`, `bairro`, `numero`, `id_regiao`, `n_enderecos`, `n_domicilios`

**`distancias_regiao/{UF}`** — uma linha por região.
`id_regiao`, `n_enderecos`, `soma_dist`, `dist_min`, `dist_max`, `acima_5km`, `acima_25km`

O bairro entra aqui porque é o que desempata quem não tem número — e é o único jeito de separar as
vias homônimas que o CNEFE empilha sob "RUA SEM DENOMINACAO". Os domicílios são contados à parte
porque um endereço pode ser uma obra ou um comércio, e a pergunta do site é onde mora gente.

### Guardas

| Guarda | Impede |
|---|---|
| `checar_crs_metrico` | vizinho mais próximo calculado em graus |
| dedup por `(id_cnefe, cod_especie)` | produto cartesiano pela chave incompleta do CNEFE |
| busca restrita ao município | endereço na divisa atribuído a outra cidade |
| `checkpoint_valido` | reaproveitar atribuição feita com outra numeração de regiões (ex.: piloto de uma UF → país inteiro) |

### Brasil

| | |
|---|---|
| Regiões-alvo | 74.142 em 5.563 municípios |
| Endereços lidos | 111.102.875 |
| Endereços atribuídos | 111.090.751 (**99,99%**) |
| Endereços sem região (municípios sem local geocodificado) | 12.124 |
| Combinações rua × bairro × número × região | 54.782.385 |

---

## Passo 32 — Índice de ruas

**Script:** `pipeline/32_montar_indice_ruas.py`
**Entrada:** `enderecos_regiao/{ano}/{UF}` (passo 31)
**Saídas:** `indice_ruas`, `ruas_dominante`, `ruas_regioes`, `ruas_bairro_regioes` e
`bairros_regioes`, todas em `dados/intermediario/`

Comprime a atribuição por endereço num índice consultável: em vez de guardar cada número, guarda só
os números onde a região muda.

### O que acontece

Cada UF é processada separadamente: a rua é chave dentro do município, então o resultado é o mesmo
de processar tudo junto, e o país inteiro cabe na memória.

**Índice de trechos:**

1. Descarta linhas sem rua ou sem região.
2. Número vazio vira 0 — a mesma marca que o CNEFE já usa para endereço sem número.
3. Ordena por município, rua e número.
4. Para cada número que aparece em mais de uma região, fica a região de maior peso.
5. Marca como início de trecho toda linha em que a rua ou a região muda em relação à anterior.
6. Guarda só esses inícios.

**Região dominante por rua:**

1. Soma o peso por município × rua × região.
2. Fica, para cada rua, a região de maior peso e a fração que ela representa.
3. Registra quantas regiões a rua atravessa.

A região dominante é a resposta quando a rua só tem uma região (o número é dispensável) ou quando
não há número nem bairro para consultar.

**Peso é domicílio, não endereço.** Todo endereço entra no índice, para que qualquer rua seja
pesquisável, mas o desempate e a ordem das opções usam só domicílios. Numa rua que não tem nenhum —
só comércio, só obra — o peso cai para a contagem de endereços, senão a rua inteira pesaria zero.

**Listas por rua e por bairro.** Só o número não basta: 23,8% dos endereços são S/N, e o usuário
pode não querer informar o dele. Então o passo também resume cada rua ambígua de duas formas, ambas
por peso: a lista de regiões da rua e a mesma lista dentro de cada bairro. Ruas de região única não
recebem lista nenhuma (a resposta é a própria região dominante), e o bairro só é gravado nas ruas
ambíguas que passam por mais de um bairro — nas outras ele não separaria nada.

Por fim, um índice de município × bairro, para quem não sabe o nome da própria rua.

### Saídas

**`indice_ruas`** — uma linha por início de trecho.
`cd_municipio_ibge`, `logradouro`, `numero_inicial`, `id_regiao`

**`ruas_dominante`** — uma linha por rua.
`cd_municipio_ibge`, `logradouro`, `id_regiao_dominante`, `n_regioes`, `fracao`

**`ruas_regioes`** — uma linha por rua ambígua × região, ordenada da mais provável para a menos.
`cd_municipio_ibge`, `logradouro`, `id_regiao`, `fracao`, `peso`

**`ruas_bairro_regioes`** — o mesmo, dentro de cada bairro.
`cd_municipio_ibge`, `logradouro`, `bairro`, `id_regiao`, `fracao`, `peso`

**`bairros_regioes`** — uma linha por município × bairro × região.
`cd_municipio_ibge`, `bairro`, `id_regiao`, `fracao`, `peso`

### Guardas

`checar_chave_unica` nas cinco saídas: um início de trecho por número em cada rua, uma linha por
rua, e uma linha por região em cada rua, rua × bairro e município × bairro.

### Brasil

| | |
|---|---|
| Combinações de entrada | 54.782.385 |
| Trechos | **6.550.061** (8× menor) |
| Ruas distintas | 2.730.382 |
| Trechos por rua, em média | 2,40 |
| Ruas com uma região só | 76,1% |
| Ruas ambíguas (com lista de regiões) | 653.081 |
| Ruas ambíguas com quebra por bairro | 342.442 |
| Linhas de município × bairro × região | 832.060, em 520.038 bairros |

---

## Passo 40 — Dados publicados

**Script:** `pipeline/40_build_dados_publicados.py`
**Entradas:** `dim_regiao` (11), `votos_regiao` (22) e as cinco tabelas do passo 32
**Saída:** pasta `publicado/`

**Quais regiões entram:** as que têm resultado **ou** as que existem na malha de referência
(`regioes_a_publicar`). A segunda metade publica região sem resultado nenhum — 5.312 delas, com a
malha de 2026 — e isso é deliberado: o índice de ruas aponta endereços para elas, e sem a região
no JSON o site resolveria um endereço para uma chave ausente. Região sem resultado não entra em
`compartilhar/`, porque o resumo do link exige um resultado de presidente, e a tela precisa dizer
"local novo, sem resultado anterior" em vez de mostrar vazio.

Monta os arquivos que o site consome: estáticos, em JSON, fatiados por município para que cada
consulta baixe só o necessário.

### O que acontece

1. Mantém só as regiões que têm voto.
2. **Calcula os resultados de cada região**, por ano, turno e cargo:
   - percentual de cada candidato sobre o **voto válido** (nominal + legenda);
   - Presidente: todos os candidatos, do mais ao menos votado;
   - Deputado Federal: os 3 mais votados (`config.N_TOP_DEPUTADOS`);
   - branco e nulo à parte, como percentual do total;
   - total de votos.
3. **Monta a chave pública de cada região** — `{zona}-{local}` do menor local de votação do grupo,
   vinda do cadastro do TSE. `id_regiao` é número de ordem e muda a cada execução, então não pode
   aparecer em link compartilhado; a chave do TSE continua a mesma na execução seguinte e sobrevive
   a uma malha nova sempre que o prédio mantiver zona e local. Os demais locais do grupo viram
   apelidos, e o link feito com qualquer um deles acha a região.
4. Grava a lista de estados e, para cada estado, a de municípios.
5. Grava, para cada município, as regiões com local de votação e resultados.
6. Grava, para cada município, as ruas com o índice de trechos e — nas ambíguas — as listas de
   regiões por rua e por bairro.
7. Grava, para cada município, o índice de bairros.
8. **Agrega Presidente por município, UF e Brasil**, para o site comparar o local com o entorno.
9. **Publica o eleitorado e a abstenção** de cada região, por ano e turno. O eleitorado vem do
   passo 10, somado dos locais que funcionam ali; a abstenção é a diferença para o comparecimento,
   que é o total de votos do cargo — quem aparece vota, em alguém, em branco ou nulo. Local fora da
   base oficial do TSE fica sem os dois: publicar zero seria dizer que ninguém faltou. Onde o
   comparecimento passa do eleitorado (voto em trânsito), a abstenção é omitida em vez de ficar
   negativa.
10. Grava, para cada município, o resumo de compartilhamento: por chave pública, o local, os dois
   primeiros de Presidente no ano mais recente e o deputado federal mais votado ali. É o que o
   servidor lê para montar a prévia do link e a imagem dela, sem abrir o arquivo de regiões — o de
   São Paulo passa de 6 MB, e a prévia é pedida a cada compartilhamento.
11. Grava os metadados (anos, turnos, contagens) e os parquets para uso analítico.

Os agregados saem de `votos_local_votacao`, e não de `votos_regiao`: neles entram **todos** os
locais, inclusive os sem coordenada. Só assim o percentual reproduz o resultado oficial — a base
geocodificada cobre 88% dos votos e erra 0,4 ponto percentual, porque o que falta não é um recorte
aleatório. A linha Brasil vem do total nacional do passo 21, que inclui o voto no exterior, e por
isso bate voto a voto com o divulgado pelo TSE. Duas guardas conferem isso: cada UF tem que bater
com a soma dos seus municípios, e o Brasil com `TOTAIS_OFICIAIS_PRESIDENTE`.

Todo JSON é gravado com `allow_nan=False`: `NaN` é aceito pelo Python e recusado pelo `JSON.parse`
do navegador, então um número indefinido derruba a publicação em vez de gerar um arquivo que o site
não consegue abrir.

### Saídas

| Arquivo | Conteúdo |
|---|---|
| `ufs.json` | sigla, nome e número de municípios de cada estado |
| `municipios/{UF}.json` | código e nome dos municípios |
| `ruas/{cd_municipio}.json` | para cada rua: `nome`, `regiao_provavel`, `n_regioes`, `confianca`, `trechos` e, nas ambíguas, `regioes` e `bairros` |
| `bairros/{cd_municipio}.json` | para cada bairro: `nome` e `regioes` — o caminho de quem não sabe o nome da rua |
| `regioes/{cd_municipio}.json` | para cada região: `id` (a chave pública), `local`, `endereco`, `lat`, `lon`, `outros_locais`, `resultados` |
| `compartilhar/{cd_municipio}.json` | por chave pública: local, município, os dois primeiros de Presidente e o deputado mais votado — o resumo que o servidor lê para a prévia do link |
| `agregados/municipios/{cd}.json`, `agregados/ufs/{UF}.json`, `agregados/brasil.json` | Presidente por ano e turno em cada território, para a comparação |
| `metadados.json` | anos e turnos existentes, ano de referência da malha, contagens |
| `regioes.parquet` | a dimensão de regiões publicada |
| `votos_regiao.parquet` | os votos por região |
| `indice_ruas.parquet` | o índice de trechos |

Uma rua real em `ruas/1100015.json` (Alta Floresta D'Oeste), com os 4 primeiros dos seus 19
trechos:

```json
{
  "nome": "AVENIDA BRASIL",
  "regiao_provavel": 52740,
  "n_regioes": 8,
  "confianca": 0.386,
  "trechos": [[0, 52739], [3, 52740], [47, 52735], [2275, 52742]],
  "regioes": [[52740, 0.386], [52739, 0.307], [52742, 0.118], [52738, 0.059]],
  "bairros": [["CENTRO",          [[52740, 0.911], [52739, 0.089], [52741, 0.0]]],
              ["PRINCESA ISABEL", [[52742, 0.529], [52738, 0.235], [52740, 0.206]]]]
}
```

`regioes` é a lista completa das regiões da rua com o peso de cada uma, e `bairros` é a mesma lista
dentro de cada bairro, ambas ordenadas da mais provável para a menos. Quem mora no Centro e não sabe
o número tem 91,1% de chance de votar na região 52740; quem mora no Princesa Isabel, na 52742. Sem o
bairro, a melhor aposta para a avenida inteira vale só 38,6%.

`confianca` é a fração do peso da rua que está na região provável. Aqui é 38,6%: a avenida atravessa
8 regiões e nenhuma concentra a maioria, então sem número nem bairro a resposta seria pouco
confiável.

`resultados` da região que atende a Avenida Brasil, 2338 (região 52737), mostrando só o primeiro
candidato de cada lista e só 2022:

```json
{
  "presidente":       { "2022": { "2": [ {"nome": "Jair Messias Bolsonaro", "partido": "PL", "votos": 2146, "pct": 72.62} ] } },
  "deputado_federal": { "2022": { "1": [ {"nome": "Fernando Rodrigues Máximo", "partido": "UNIÃO", "votos": 504, "pct": 18.03} ] } },
  "nao_nominal":      { "presidente": { "2022": { "2": { "branco": {"votos": 30, "pct": 0.98}, "nulo": {"votos": 70, "pct": 2.29} } } } },
  "total_votos":      { "presidente": { "2022": { "2": 3055 } } }
}
```

`pct` de candidato é sobre o voto válido; `pct` de branco e nulo é sobre o total. Por isso
2.146 votos são 72,62% (de 2.955 válidos) e 30 brancos são 0,98% (de 3.055).

### Consultando

[`consulta_regiao.py`](consulta_regiao.py) lê só `publicado/`, exatamente como o site fará, e é a
especificação executável da busca:

```bash
python consulta_regiao.py --municipio 1100015 --rua "AVENIDA BRASIL" --numero 2338
python consulta_regiao.py --municipio 1100205 --rua "RUA SEM DENOMINACAO" --bairro ABUNA
python consulta_regiao.py --municipio 1100205 --bairro ABUNA          # não sei o nome da rua
python consulta_regiao.py --municipio 1100015 --listar-ruas BRASIL
```

A sequência de decisão, em ordem:

1. **Número vazio, não numérico ou zero conta como "sem número"** — no CNEFE, zero é a marca de S/N.
2. **Rua de região única** → responde, `exata`. Número e bairro são irrelevantes.
3. **Bairro informado** → restringe as regiões candidatas às daquele bairro. Bairro que não existe
   na rua é ignorado (`bairro_ignorado`). Sobrando uma região só, responde `exata`.
4. **Número informado** → busca binária no índice de trechos, limitada às candidatas: `exata` se o
   número é início de trecho, `interpolada` se cai entre dois. Se o trecho encontrado é de outro
   pedaço da rua (outro bairro), caminha para os lados até o primeiro compatível; não havendo
   nenhum, descarta o número e marca `conflito_numero_bairro`.
5. **Sem número** → a região de maior peso entre as candidatas, `provavel`.
6. **Sem rua** → as regiões daquele bairro no município.

A resposta sempre traz `opcoes`: as regiões possíveis com o peso de cada uma, da mais provável para
a menos, mesmo quando é uma só. Assim o site não precisa de dois caminhos de código — mostra a
resposta e, havendo mais de uma opção, a lista de locais para o usuário escolher.

Na prática, em Porto Velho: `RUA SEM DENOMINACAO` sozinha devolve 27 opções; com o bairro ABUNA,
devolve uma, `exata`. `AVENIDA CALAMA` devolve 17; com EMBRATEL, uma; com APONIA, três.

`id_regiao` é numerado em sequência sobre o recorte de UFs e o ano de referência: mudar qualquer um
dos dois renumera as regiões. O site não deve guardar ids de uma versão publicada para outra.

Para experimentar a consulta numa interface, `beta.html` faz o mesmo percurso no navegador, lendo os
mesmos arquivos por `fetch`. Basta servir a pasta do projeto (`python -m http.server 8787`) e abrir
`http://127.0.0.1:8787/beta.html`.

### Brasil

74.142 regiões publicadas em 16.722 arquivos, 809 MB: `ruas/` 419 MB, `regioes/` 213 MB,
`bairros/` 31 MB e 146 MB de parquets analíticos.

O que o navegador baixa é bem menos, porque JSON comprime muito: o `ruas/` da cidade de São Paulo,
o maior do país, tem 9,7 MB crus e **2,1 MB com gzip** (o `regioes/` da mesma cidade, 6,1 MB, cai
para 0,7 MB). No total, os 809 MB viram cerca de 144 MB servidos.

---

## Validação dos arquivos publicados

**Script:** `qualidade/validar_publicado.py`
**Saída:** nenhuma — ou uma falha com a lista de problemas

As guardas dos passos checam cada transformação por dentro. Esta olha o produto, porque um arquivo
pode estar correto para o Python e ainda assim ser inútil para o site. São quatro verificações sobre
os 16.717 arquivos de `publicado/`:

1. **JSON estrito.** Todo arquivo abre num parser que recusa `NaN` e `Infinity`. O Python aceita os
   dois, na leitura e na escrita; o `JSON.parse` do navegador recusa o arquivo inteiro.
2. **Integridade referencial.** Toda região citada em `ruas/` e em `bairros/` existe no `regioes/`
   daquele município; todo município listado tem os seus arquivos; `ufs.json` bate com as listas.
3. **Percentuais.** Para Presidente, onde todos os candidatos são publicados, a soma dos
   percentuais fecha 100; nenhum percentual sai de 0–100; nenhum voto é negativo.
4. **Listas de regiões.** As frações somam 1 e vêm em ordem decrescente, na rua e no bairro.
5. **Agregados.** Nenhum candidato aparece duas vezes na mesma lista, a lista tem tamanho de
   eleição presidencial (até 40 nomes), os percentuais fecham 100, os válidos não passam do total,
   e a soma das UFs cabe dentro do Brasil sem ultrapassá-lo.

A quinta checa invariantes que valem para qualquer resultado eleitoral, independentemente de como
ele foi calculado. É o tipo de conferência que pega o erro mais perigoso deste pipeline: um número
plausível, com cara de certo, produzido por uma agregação que faltou.

A tolerância da quarta acompanha o tamanho da lista: as frações são publicadas com três casas, então
uma região de peso desprezível vira `0.0` e a soma fica curta. Numa rua com centenas de regiões — as
vias sem nome de São Paulo chegam a 715 — isso é arredondamento, não defeito.

---

## Validação ponta a ponta

**Script:** `qualidade/validar_indice_ruas.py`
**Saída:** `publicado/validacao_indice_ruas.json`

Os passos anteriores têm guardas próprias, mas cada uma checa uma etapa isolada. Esta refaz o
caminho completo do usuário e mede o efeito acumulado de todas as aproximações.

### O que acontece

1. Sorteia 5.000 endereços reais do CNEFE por UF (semente fixa, reprodutível), lendo o arquivo em
   blocos — o CNEFE de São Paulo sozinho tem 3,8 GB.
2. Calcula a **verdade espacial** de cada um: a região mais próxima da coordenada real.
3. Consulta o índice publicado pelos **quatro caminhos** que o site pode percorrer — só a rua,
   rua + bairro, rua + número, e os três juntos —, usando a mesma função de `consulta_regiao.py`,
   não uma reimplementação.
4. Compara cada resposta com a verdade espacial, e separa o resultado entre todos os endereços e
   só os domicílios. A separação importa: o índice pesa por domicílio, e a amostra tem também
   comércio, obra e escola.

### Brasil

134.981 endereços sorteados (5.000 por UF), rua encontrada no índice em 100% deles. Acerto médio
das UFs, por caminho de consulta:

| O que o usuário informa | Todos os endereços | Só domicílios |
|---|---:|---:|
| Só a rua | 76,54% | 77,72% |
| Rua + bairro | 86,25% | 87,19% |
| Rua + número | 91,59% | 92,77% |
| Rua + bairro + número | **95,77%** | **96,54%** |

A coluna de domicílios é a que descreve o usuário do site, e é a maior: o índice desempata por
domicílio, então acerta mais onde mora gente do que em obras e comércios.

O bairro vale mais que o número justamente onde o número falta. Em Goiás, rua + bairro acerta 89,5%
contra 84,0% de rua + número; no Maranhão, 88,9% contra 83,1%. Em São Paulo é o contrário: 87,2%
contra 97,5%. Por UF, o caminho completo vai de 93,5% em Goiás a 98,7% em São Paulo.

Detalhes em [LIMITACOES.md](LIMITACOES.md).

---

## Relatório de cobertura

**Script:** `qualidade/relatorio_cobertura.py`
**Saída:** `publicado/cobertura.json`

Reúne num arquivo só as três perdas independentes do pipeline, para o site mostrá-las em vez de
apresentar os números como exatos.

| Perda | Onde acontece | Brasil |
|---|---|---|
| Local de votação sem coordenada | artefato importado + base oficial do TSE | 99,7% com coordenada |
| Voto em local sem coordenada | passo 22 | 99,5% dos votos em alguma região |
| Endereço ambíguo no índice | passo 32 | 15,9% dos endereços em chave ambígua; 23,8% sem número |

Traz também o acerto medido pela validação ponta a ponta, a cobertura da geocodificação por UF e o
recorte da execução.

---

## Diagnóstico de distâncias

**Script:** `qualidade/diagnostico_distancias.py`
**Saída:** `publicado/diagnostico_distancias.json`

O vizinho mais próximo sempre devolve alguém. Se um local de votação tiver coordenada errada, ele
continua sendo o mais próximo de algum endereço — só que a quilômetros de distância. Nenhuma guarda
enxerga isso, porque não há resposta ausente para reclamar.

A partir do resumo gravado no passo 31, o diagnóstico publica o retrato nacional e três listas.

**O retrato.** A distância média entre um endereço e o seu local de votação é de **1.673 m**; metade
das regiões atende endereços a menos de 730 m em média. 8,0% dos endereços estão a mais de 5 km e
0,63% a mais de 25 km — isso é o país, não defeito do dado.

**Distância absoluta não aponta erro.** As regiões com os endereços mais distantes são as de
municípios enormes com poucos locais geocodificados: Jutaí (AM) tem 4 regiões para um território do
tamanho de um país, Barcelos (AM) tem 6. Centenas de quilômetros ali são o normal. A lista
`regioes_mais_distantes` serve para dimensionar isso, não para acusar.

**O sinal útil é relativo:** `regioes_que_destoam_do_municipio` compara cada região com a mediana das
outras do mesmo município. Uma região 100 vezes acima da mediana municipal é candidata a conferência
— pode ser uma escola ribeirinha legítima, pode ser coordenada errada; o diagnóstico não decide.

**Uma invariante, que vale para 2026.** A terceira lista, `regioes_sem_endereco_na_coordenada`, está
vazia hoje: as 74.142 regiões têm um endereço do CNEFE exatamente na coordenada, o que é esperado,
já que a geocodificação saiu do próprio CNEFE. Ela deixa de estar vazia quando entrar coordenada de
outra fonte — a latitude/longitude do próprio TSE, por exemplo, para locais novos. Aí uma distância
mínima alta é coordenada a conferir.

É um diagnóstico, não uma guarda: ele não derruba o pipeline. Transformar distância em guarda geraria
falso positivo em todo município amazônico.

---

## Testes

```bash
pytest tests/
```

87 testes, cerca de um segundo, com dados fabricados — não precisam dos arquivos brutos.

| Arquivo | Cobre |
|---|---|
| `tests/test_validacoes.py` | cada guarda de `qualidade/validacoes.py`, com as particularidades do dado bruto que elas tratam |
| `tests/test_votacao.py` | a classificação do tipo de voto, incluindo a legenda com sequencial vazio do DF em 2018 |
| `tests/test_publicacao.py` | a agregação de votos por território: soma por candidato, percentual sobre o válido, ordem, separação por ano e turno |
| `tests/test_validar_publicado.py` | as conferências do produto: região citada que não existe, rua ambígua sem lista, frações que não fecham, lista fora de ordem, percentuais fora da faixa, `NaN` recusado |
| `tests/test_consulta.py` | a sequência de decisão da consulta: limites de trecho, número abaixo do primeiro, zero como S/N, bairro que resolve, bairro inexistente, número que cai em trecho de outro bairro, ordem das opções |

---

## Mudando o recorte

**Menos estados.** Liste as UFs em `UFS_ALVO` no `config.py` e rode tudo de novo. O padrão,
`None`, é o país inteiro. O passo 31 pula as UFs já processadas, depois de conferir que o checkpoint
foi feito com a mesma numeração de regiões — mudar o recorte renumera as regiões, e aí a UF é
refeita.

**Resultado de 2026.** A malha de 2026 já está pronta: o passo 10 lê
`eleitorado_local_votacao_2026`, que traz nome, endereço e coordenada de 95.116 locais, e o passo 11
a incorpora. O que falta é o voto, e ele chega por dois caminhos.

*Na noite da apuração*, pelos **boletins de urna** — a única fonte com granularidade de local de
votação enquanto não sai o CSV por seção. Todo o processo está em
[coleta_2026/README.md](coleta_2026/README.md), e a noite é um comando:
`python -u coleta_2026/domingo.py`, que coleta, converte, confere contra o resultado oficial do TSE,
troca o `ANO_REFERENCIA_MALHA` e roda o pipeline do passo 22 em diante. O passo 21 **não** roda: ele
reconstrói a tabela de votos a partir dos CSVs e levaria 2026 junto.

*Semanas depois*, quando o TSE publica a votação por seção, o caminho volta ao normal do projeto:

1. Coloque os arquivos nas pastas de sempre: `votacao_presidente/votacao_secao_2026_BR.zip`,
   `votacao_uf/2026/votacao_secao_2026_{UF}.zip` e `candidatos/2026/consulta_cand_2026.zip`.
2. Em `config.py`, acrescente 2026 em `ANOS_ELEICAO` e o total oficial do 1º turno em
   `TOTAIS_OFICIAIS_PRESIDENTE[2026][1]`. Sem o total, a validação oficial é pulada com aviso.
3. `ANO_REFERENCIA_MALHA = 2026`, se ainda não estiver.
4. `python rodar_pipeline.py`. O passo 21 reescreve a tabela de votos a partir dos CSVs, e com isso
   os números dos boletins dão lugar aos oficiais — que trazem a classificação final, inclusive os
   votos que a urna gravou como nominais e a totalização considerou nulos.

Para medir os locais de 2026 contra a geocodificação antes de trocar a malha:
`python qualidade/comparar_locais.py --ano 2026`. Mostra, por UF, quantos locais têm o mesmo id e o
mesmo nome ou endereço, quantos têm id conhecido com nome e endereço diferentes e quantos são novos.

Sem a troca do `ANO_REFERENCIA_MALHA`, 2026 entra como mais um ano sobre a malha de 2022: votos de
locais que não existiam em 2022 são realocados para o local de 2022 mais próximo.

O 2º turno não exige mudança de código: os turnos saem do próprio arquivo. Basta atualizar o arquivo
bruto, cadastrar `TOTAIS_OFICIAIS_PRESIDENTE[2026][2]` e rodar de novo a partir do passo 21.

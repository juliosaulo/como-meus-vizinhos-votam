# Procedência: `locais_votacao_2018_2022.parquet`

Este é o **único dado deste projeto que não é produzido pelo pipeline daqui**. Ele traz a
coordenada de cada local de votação, e foi construído num trabalho anterior do mesmo autor —
reaproveitado em vez de refeito porque sua construção envolveu um classificador supervisionado e
mais de 24 mil decisões tomadas manualmente, uma a uma.

Este documento descreve como essas coordenadas foram obtidas, para que quem consome o projeto possa
julgar o quanto confiar nelas.

## O que o arquivo contém

| | |
|---|---|
| Linhas | 93.658 locais de votação |
| Recorte | locais usados nas eleições de 2018 e/ou 2022 |
| Com coordenada final | **78.568 (83,9%)** |
| Coordenadas distintas | 74.552 (locais diferentes podem dividir o mesmo prédio) |
| Abrangência | 5.570 municípios, 27 UFs + `ZZ` (voto no exterior) |

Colunas: `id_local_votacao`, `sg_uf`, `nm_municipio`, `cd_municipio_ibge`,
`nm_local_votacao_consolidado`, `ds_local_votacao_endereco_consolidado`,
`status_geocodificacao`, `latitude_final`, `longitude_final`.

`id_local_votacao` = `SG_UF` + `CD_MUNICIPIO` + `NR_ZONA` + `NR_LOCAL_VOTACAO`, separados por
sublinhado, com o código do município sempre em 5 dígitos — o TSE o exporta sem zero à esquerda em
alguns anos, e sem a normalização o mesmo prédio viraria dois registros distintos.

## O problema que ele resolve

Saber em que prédio cada seção funciona não basta: é preciso saber **onde esse prédio fica**, para
responder qual local de votação atende um endereço qualquer — a pergunta deste projeto.

A solução foi geocodificar cada local casando seu nome e endereço contra o **CNEFE** (Cadastro
Nacional de Endereços para Fins Estatísticos, do IBGE), que tem ~111 milhões de endereços com
coordenada. É um problema de *record linkage* entre duas bases sem chave comum, com texto sujo dos
dois lados.

## Duas fontes de coordenada, e por que as duas ficam

O TSE também publica a coordenada dos locais, no arquivo `eleitorado_local_votacao_AAAA` dos dados
abertos. As duas fontes foram medidas uma contra a outra, e as duas ficam. O que segue são os
números dessa medição — inclusive os que não favorecem este artefato.

### Cobertura: o dado oficial não estava lá quando este artefato foi construído

Contando só coordenada **utilizável**, isto é, fora os sentinelas `-1` e os pontos fora do
território brasileiro que o passo 10 descarta:

| edição | locais | com coordenada | sem |
|---|---:|---:|---:|
| 2018 | 94.904 | 79,3% | 19.629 |
| 2022 | 92.427 | 92,8% | 6.611 |
| 2026 | 95.116 | **99,1%** | 896 |

A base oficial melhorou muito em 2026. Para o recorte deste artefato — locais de 2018 e 2022 — ela
deixava de 7% a 21% dos locais sem coordenada, e é por isso que o casamento com o CNEFE não é
trabalho redundante.

### Concordância: o maior teste externo que este artefato já teve

73.102 locais têm coordenada nas duas fontes. A distância entre os dois pontos:

| as duas concordam dentro de | |
|---|---:|
| 10 m | 17,1% |
| 50 m | 55,0% |
| 100 m | 63,6% |
| 500 m | 76,9% |
| 5 km | **88,9%** |

Mediana de 38 m. Dois métodos independentes — classificador supervisionado contra o CNEFE de um
lado, cadastro da Justiça Eleitoral do outro — caem no mesmo prédio em quase 9 de cada 10 casos. É
evidência a favor dos dois, sobre mais do que o triplo dos 21.890 casos que foram rotulados à mão,
e vinda de uma fonte que não participou de nenhuma etapa da construção.

A divergência também não é efeito de comparar safras diferentes: contra as três edições do arquivo
oficial o resultado é praticamente o mesmo — 11,06% (2018), 11,08% (2022) e 11,12% (2026) de locais
acima de 5 km.

### Onde discordam, nenhuma das duas é oráculo

Os 8.129 locais que divergem mais de 5 km reúnem 7,2 milhões de eleitores, 4,6% do eleitorado de
2026. Para saber quem erra, montou-se um árbitro independente das duas: **quantos endereços do
CNEFE existem em volta de cada um dos dois pontos**. Local de votação real fica cercado de
endereços. Endereços a menos de 5 m são descartados da contagem dos dois lados, porque a coordenada
deste artefato é ela própria um endereço do CNEFE — sem esse descarte a comparação seria circular.

Rodado nos 8.129 casos, nas 27 UFs, consultando 8,1 milhões de endereços do CNEFE em 01/10/2026:

| | ponto deste artefato | ponto do TSE |
|---|---:|---:|
| endereços em 250 m (mediana) | 40 | 52 |
| endereços em 1 km (mediana) | 130 | 148 |
| ponto plausível por uma ordem de grandeza | 1.017 (12,5%) | 1.011 (12,4%) |
| isolado: nenhum endereço em 1 km | **13 (0,2%)** | 413 (5,1%) |

Empate técnico — 1.017 contra 1.011, com 75% dos casos sem veredicto. **Não há base para afirmar
que a coordenada deste artefato é melhor que a oficial onde as duas discordam.** A única assimetria
clara está no extremo: o arquivo oficial coloca 413 locais, com 241 mil eleitores, onde não existe
um só endereço num raio de 1 km; aqui isso acontece 13 vezes.

Os erros têm assinaturas diferentes, e o árbitro as distingue caso a caso:

- **Deste lado, rua homônima.** A Escola Municipal Charles Anderson Weaver, no Rio, tem o mesmo
  texto de endereço nas duas fontes — "RUA CARLOS PACHECO AVILA S/N" — e os pontos distam 26,5 km:
  o casamento pegou a rua certa no pedaço errado da cidade (128 endereços em volta, contra 2.000 do
  ponto oficial). O mesmo padrão aparece em Brasília e em Salvador.
- **Do lado oficial, ponto largado.** Em Salvador, três escolas têm 1.999 endereços em volta do
  ponto daqui e de 2 a 10 em volta do oficial.

### O TSE também discorda de si mesmo

A coordenada oficial não é um valor fixo: o TSE regera esses arquivos, e o mesmo local muda de lugar
entre edições.

| par de edições | locais nas duas | coordenada idêntica | move > 50 m | move > 5 km | pior caso |
|---|---:|---:|---:|---:|---:|
| 2018 → 2022 | 71.104 | 90,2% | 3,07% | 0,31% | 193 km |
| 2022 → 2026 | 80.991 | **58,1%** | 7,37% | 1,21% | **1.735 km** |

Isso **não** explica a divergência de 11% — em 87% dos casos divergentes o ponto oficial havia sido
estável nas três edições. Mas define o que significa "adotar a base oficial": adotar um valor que se
move, inclusive por quilômetros, a cada regeração.

### Dois limites deste artefato, para ficar registrado

**Pontos degenerados** — vários locais sobre a mesma coordenada — são mais comuns aqui: 9,1% dos
locais, contra 0,8% a 1,3% no arquivo oficial. Parte é legítima (duas seções no mesmo prédio), parte
é casamento colando prédios diferentes no mesmo endereço.

**A divergência se concentra onde a confiança do classificador era menor**, o que é coerente com o
processo:

| status | locais comparados | diverge > 5 km |
|---|---:|---:|
| `top1_auto` | 31.706 | 6,4% |
| `auto_confiante_pre_ml` | 33.292 | 12,3% |
| `top2_promovido` | 2.978 | 17,4% |
| `revisao_manual_aceito` | 5.126 | **29,2%** |

O status, porém, **não prevê quem está certo**: entre os casos com veredicto do árbitro, a fatia que
aponta o ponto oficial é de 14,5% em `top1_auto` — o status mais confiável — e 11,0% em
`revisao_manual_aceito`. Não existe regra por status que melhore a malha; a correção, quando vier,
tem de ser caso a caso.

### A decisão

A divisão de trabalho, implementada no passo 10 e aplicada no passo 11:

| Situação | Coordenada usada |
|---|---|
| local com coordenada neste artefato | **a daqui** (CNEFE) |
| local sem coordenada aqui, mas na base oficial | a do TSE |
| local que só existe em eleição posterior (2026) | a do TSE |
| local novo a menos de 50 m de um ponto já conhecido | a do ponto conhecido, por ser o mesmo prédio |

O complemento oficial elevou a cobertura de **83,9% para 99,7%** dos locais, e a parcela dos votos
que cai em local com coordenada de 88% para **99,5%**.

Por que não trocar tudo pela coordenada oficial, já que em 2026 ela cobre 99,1%:

1. **Não há ganho medido.** O árbitro dá empate nos casos em que as duas discordam.
2. **O custo é grande e certo.** Medido em quatro municípios, a troca mudaria o local atribuído a
   **16% a 37% dos endereços** — e de 57% a 90% dessa mudança vem da minoria de pontos mal
   colocados, não do deslocamento típico de ~38 m.
3. **A base oficial se move.** 42% dos locais mudaram de coordenada entre 2022 e 2026; os ids
   públicos dos links do site, e a malha de regiões, passariam a depender disso.
4. **A coordenada casada é um endereço do CNEFE.** As regiões ficam exatamente sobre um ponto do
   mesmo cadastro contra o qual o passo 31 mede distância, e é isso que faz de `dist_min` uma
   guarda: local real tem endereço colado nele.

E há um papel que só este artefato pode cumprir: **arbitrar a base oficial**. Não existe terceira
fonte de coordenada de local de votação no Brasil. Os 413 pontos isolados do arquivo oficial só
foram encontrados porque havia um segundo ponto para comparar.

O relatório do pipeline que faz essa comparação é `publicado/divergencia_coordenadas.json`
(`qualidade/comparar_coordenadas.py`). Ele usa um critério mais fraco que o árbitro desta seção —
qual dos dois pontos foge da nuvem de pontos do próprio município —, e por isso devolve "indefinido"
em 5.072 dos 8.710 casos que lista. O árbitro de densidade de endereços decide 2.028 deles, e
substituí-lo no relatório é uma melhoria pendente.

### Nota de histórico

Até setembro de 2026 este documento afirmava que o TSE não publicava a coordenada dos locais, e uma
versão posterior afirmou que publicava para 100% deles. As duas afirmações estavam erradas, e as
medições acima as substituem. Não é possível reconstruir, daqui, se a coluna já vinha preenchida
quando o artefato foi construído: o TSE regera os arquivos, e a cópia de 2022 disponível hoje foi
gerada em 30/09/2024.

## Como cada coordenada foi decidida

| Status | Locais | Quem decidiu |
|---|---:|---|
| `auto_confiante_pre_ml` | 35.692 | regra determinística, antes de qualquer modelo |
| `top1_auto` | 34.150 | classificador, no melhor candidato |
| `top2_promovido` | 3.252 | classificador, recuperando o 2º colocado |
| `revisao_manual_aceito` | 5.474 | julgamento humano |
| **Total com coordenada** | **78.568** | |
| `revisao_manual_rejeitado` | 8.792 | julgamento humano (sem coordenada) |
| `rejeitado_auto` | 5.257 | classificador (sem coordenada) |
| `revisao_manual_incerto` | 786 | julgamento humano (sem coordenada) |
| `sem_candidato` | 255 | nenhum candidato CNEFE no município |

### 1. Preparação e match em cascata

Limpeza e normalização dos dois lados (nome, endereço, número), de-para entre o código de município
do TSE e o do IBGE, e geração de candidatos **dentro do mesmo município**. Para cada par
calculou-se score de nome, de endereço e de número, combinados 50/50 quando só há nome e endereço,
e 40/40/20 quando o número bate exatamente — número diferente não pontua, apenas fica registrado.

Dois refinamentos importantes:

- **Agrupamento espacial de candidatos.** Registros do CNEFE a poucos metros um do outro (mesmo
  prédio, ou lotes contíguos) são tratados como um conjunto, permitindo que a melhor evidência de
  nome venha de um registro e a de endereço de outro. A coordenada final é sempre a do
  representante do grupo, nunca uma mistura.
- **Penalização de nome genérico.** "ESCOLA MUNICIPAL" casa com quase tudo. A penalização é
  aplicada **apenas quando o candidato do CNEFE é mais genérico que o nome do TSE**, o que preserva
  o identificador próprio ("ESCOLA GETÚLIO VARGAS") e derruba só quem ficou no tipo institucional.

Aceite automático nesta fase: score ≥ 88 **e** vantagem ≥ 5 sobre o segundo colocado.

### 2. Classificador supervisionado

Os 67.492 casos que não passaram no corte automático foram para um classificador GBM
(`HistGradientBoostingClassifier`), treinado sobre casos rotulados à mão em amostra estratificada.
São dois modelos: um completo, com features que comparam o 1º e o 2º candidato, e um reduzido, que
reavalia o 2º colocado como candidato de recuperação. Os limiares de aceite e rejeição saem da
curva de precisão-recall, com intervalo de confiança de Wilson — sem ele, um corte de alta precisão
poderia ser fixado com base em poucas dezenas de casos.

### 3. Engenharia de features

Cinco features carregam o ganho da versão final: genericidade também do lado do TSE; indicador de
endereço-placeholder ("rua principal", "estrada geral") dos dois lados; indicador de nome de
homenagem nacionalmente repetido; generalização da lógica de qualificadores numerados (Quadra, KM,
Setor); e duas métricas de sobreposição cruzada entre o nome de um lado e o endereço do outro.

Resultado: average precision **0,984**, Brier 0,077, e automação de 67,6% do universo. O indicador
de endereço-placeholder do lado do CNEFE aparece entre as features mais importantes.

### 4. Rotulagem manual

Os 21.890 casos restantes foram rotulados integralmente, com um protocolo deliberadamente cego:
**cada caso é julgado só pelo nome e pelo endereço dos dois lados**, sem acesso a score, distância
ou metadado de agrupamento. A razão é evitar que o rótulo seja influenciado pelos mesmos sinais que
o modelo usa como entrada — um rótulo assim contaminado inflaria artificialmente a qualidade
medida.

Critérios consolidados: aceitar quando o endereço coincide, mesmo com nome divergente; rejeitar
quando os endereços claramente divergem, incluindo qualificadores numerados com número diferente;
marcar como incerto quando nome e endereço são genéricos demais para decidir.

Distribuição final: 13.760 rejeitados (62,9%), 6.715 aceitos (30,7%), 1.415 incertos (6,5%) — taxa
de rejeição alta e esperada, já que essa fila concentra o que sobrou depois de o classificador ter
separado os casos fortes.

## Qualidade e limites

- **Precisão dos aceites automáticos**: validação manual por amostra indicou ~**98,7%** de acerto.
  O restante se comporta como ruído de mensuração territorial — alguns endereços ficam associados
  ao local de votação errado.
- **Cobertura desigual entre estados**: de 53,2% no Distrito Federal e 66,6% no Pará a 93,3% em São
  Paulo (medido neste projeto, sobre o recorte de 2018/2022). A diferença acompanha a qualidade do
  endereçamento em áreas rurais e periferias — por isso este projeto publica cobertura por UF e por
  município, em vez de um número nacional único.
- **`ZZ` (voto no exterior)** tem 0% de cobertura, por construção: são locais em Boston, Tóquio,
  Bruxelas, sem correspondência possível no CNEFE. Ficam fora deste projeto.
- **Local sem coordenada não significa match ruim**: a maioria simplesmente não tinha nome ou
  endereço aproveitável na base bruta do TSE, uma limitação anterior a todo o processo.

## Como reproduzir

O pipeline completo de geocodificação está no projeto de origem, nos scripts numerados 1 a 7
(preparação e match), 28 a 31 (treino, produção e consolidação) e 33 (recorte para 2018/2022), mais
a base de rotulagem manual versionada. É um processo de várias horas de computação e várias semanas
de rotulagem.

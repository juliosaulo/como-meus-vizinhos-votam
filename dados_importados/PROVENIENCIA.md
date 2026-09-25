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

O TSE informa em que prédio cada seção eleitoral funciona — com nome e endereço em texto livre —
mas **não publica a coordenada** desse prédio. Sem coordenada não há como saber qual local de
votação atende um endereço qualquer, que é exatamente a pergunta deste projeto.

A solução foi geocodificar cada local casando seu nome e endereço contra o **CNEFE** (Cadastro
Nacional de Endereços para Fins Estatísticos, do IBGE), que tem ~111 milhões de endereços com
coordenada. É um problema de *record linkage* entre duas bases sem chave comum, com texto sujo dos
dois lados.

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

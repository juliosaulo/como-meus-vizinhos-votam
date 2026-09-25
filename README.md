# Como votou a minha região

Digite um endereço no Brasil e descubra **onde você provavelmente vota** e **como a sua vizinhança
votou** para Presidente em 2018 e 2022 — e quais deputados federais saíram na frente ali.

```
$ python consulta_regiao.py --municipio 1100015 --rua "AVENIDA BRASIL" --numero 2338

ALTA FLORESTA D OESTE/RO — AVENIDA BRASIL, 2338
  confiança: exata

  Você vota em: COLÉGIO TIRADENTES DA POLÍCIA MILITAR (CTPM)
  RUA NEREU RAMOS, 4581
  mapa: -11.934376, -62.003306

  Presidente 2022 · 2º turno
     72.62%  Jair Messias Bolsonaro (PL)
     27.38%  Luiz Inácio Lula Da Silva (PT)

  Deputado Federal 2022 — mais votados aqui
     18.03%  Fernando Rodrigues Máximo (UNIÃO)
      7.73%  Silvia Cristina Amancio Chagas (PL)
      7.33%  Lucio Antonio Mosquini (MDB)
```

O número da casa é opcional — e o site não precisa pedi-lo. Quando a rua atende mais de um local,
quem desempata é o bairro:

```
$ python consulta_regiao.py --municipio 1100205 --rua "RUA SEM DENOMINACAO" --bairro ABUNA

PORTO VELHO/RO — RUA SEM DENOMINACAO (ABUNA), s/n
  confiança: exata

  Você vota em: MARECHAL CANDIDO RONDON - ESCOLA ESTADUAL - DISTRITO ABUNÃ
  RUA TIRADENTES, S/N, FONE (69) 3236-1162
```

Sem o bairro, essa rua teria 27 respostas possíveis: o CNEFE registra todas as vias sem nome de
Porto Velho sob o mesmo rótulo. Não havendo desempate, a consulta devolve a lista de locais,
ordenada pela chance de cada um, para a pessoa escolher.

Este repositório contém o **pipeline de dados** que produz essa resposta, para os 27 estados. O site
que vai consumi-lo é uma etapa seguinte; aqui está a engenharia que torna a pergunta respondível.

## O problema

O TSE publica o resultado de cada urna, e a urna fica num prédio com nome e endereço. Mas ninguém
publica a coordenada desse prédio, nem qual eleitor vota onde — e a segunda coisa é dado pessoal,
que não deveria mesmo ser pública.

Então "como votou a minha região" não é uma consulta: é uma inferência que precisa de três pontes.

1. **Do endereço à coordenada.** O CNEFE (IBGE) tem 111 milhões de endereços brasileiros com
   latitude e longitude.
2. **Do local de votação à coordenada.** Esta é a parte difícil, e entra pronta, como artefato
   importado: pareamento de texto entre o cadastro do TSE e o CNEFE, com classificador
   supervisionado e mais de 24 mil decisões manuais. Método completo em
   [PROVENIENCIA.md](dados_importados/PROVENIENCIA.md).
3. **Da coordenada ao local de votação.** Cada endereço é atribuído ao local de votação mais
   próximo dentro do próprio município, por uma árvore de vizinho mais próximo (`scipy.cKDTree`)
   sobre coordenadas projetadas em metros.

A terceira ponte é uma **aproximação**, e o projeto inteiro se apoia nela: presume-se que quem mora
perto de um local de votação vota nele. É razoável — a Justiça Eleitoral aloca por proximidade —
mas não é a verdade individual de ninguém. O site mostra o local de votação num pino no mapa
justamente para deixar essa suposição visível, em vez de escondê-la atrás de uma mancha colorida.

## Como funciona

```
  locais de votação geocodificados          CNEFE (IBGE)           votação por seção (TSE)
   (importado — PROVENIENCIA.md)         111M endereços com          Presidente e Deputado
                 │                        rua, número e              Federal, 2018 e 2022
                 │                          coordenada                        │
                 ▼                               │                            ▼
       11 · dimensão de regiões                  │              21 · classifica voto, junta
   agrupa locais por coordenada                  │                 partido, monta a chave
     (mesmo prédio = uma região)                 │                            │
                 │                               │                            ▼
                 ├───────────────────────────────┤              22 · votos por região
                 │                               │             soma e realoca o histórico
                 ▼                               ▼                            │
      31 · endereço → região          32 · índice de ruas                     │
      vizinho mais próximo,        comprime por trecho de                     │
        município a município       numeração (8× menor)                      │
                 │                               │                            │
                 └───────────────────────────────┴────────────────────────────┘
                                                 │
                                                 ▼
                                    40 · dados publicados
                                 JSON por município, prontos
                                       para o site
```

Cada passo — entradas, o que faz, saídas, guardas e números da execução nacional — está descrito em
[PIPELINE.md](PIPELINE.md).

## Onde fica o quê

| | |
|---|---|
| [PIPELINE.md](PIPELINE.md) | **como** cada passo funciona, com os números de cada etapa |
| [METODOLOGIA.md](METODOLOGIA.md) | **por que** as decisões de método são estas, e quais são as fontes |
| [LIMITACOES.md](LIMITACOES.md) | o que o dado não cobre e onde ele erra |
| [PROVENIENCIA.md](dados_importados/PROVENIENCIA.md) | de onde vêm as coordenadas dos locais de votação |
| `pipeline/` | os passos, numerados na ordem em que rodam |
| `qualidade/` | as guardas, a validação ponta a ponta e os relatórios |
| `publicado/` | o produto: JSON por município, prontos para o site |
| [`consulta_regiao.py`](consulta_regiao.py) | a consulta de referência, que lê só `publicado/` |
| [`beta.html`](beta.html) | uma página de demonstração, para ver o dado funcionando |

## Rodando

```bash
pip install -r requirements.txt
python rodar_pipeline.py --listar     # ver os passos
python rodar_pipeline.py              # rodar tudo — Brasil inteiro, cerca de uma hora
pytest tests/                         # 76 testes, ~1s, sem precisar dos dados
```

O recorte (UFs, anos, cargos) fica em [`config.py`](config.py). O padrão é o Brasil inteiro, que
roda numa máquina com 16 GB de RAM; para um teste rápido, `UFS_ALVO = ["RO"]` roda em cerca de um
minuto e meio.

Os dados brutos (~8 GB de TSE e CNEFE) não estão no repositório. O pipeline os espera em
`dados/bruto/`; quem já tiver os arquivos em outro lugar aponta com a variável de ambiente
`VOTO_REGIAO_BRUTO`. As fontes estão documentadas em [METODOLOGIA.md](METODOLOGIA.md).

### O que este repositório versiona

O produto completo tem 809 MB em 16.723 arquivos, e é reproduzível a partir de fontes públicas. Por
isso o repositório carrega o código, a documentação, o artefato importado com as coordenadas dos
locais de votação (5,6 MB) e os arquivos leves do produto — índice de estados e municípios,
cobertura, validação e diagnóstico.

Dos dados por município vão **dois de amostra**, Alta Floresta D'Oeste e Porto Velho (RO), o
bastante para abrir a página de demonstração sem rodar nada. Consultar qualquer outro município
exige rodar o pipeline.

Para ver o dado funcionando sem escrever código, há uma página de demonstração que consome os JSON
publicados do mesmo jeito que o site fará:

```bash
python -m http.server 8787        # a partir da pasta do projeto
# abra http://127.0.0.1:8787/beta.html
```

Ela pede a rua, oferece os bairros quando a rua atende mais de um local, pede o número só quando
ainda for preciso, e mostra a lista de locais possíveis encolhendo a cada filtro.

## Qualidade

O pipeline **falha em vez de publicar** quando um número não fecha. A validação mais forte é a
mais simples: o total nacional apurado para Presidente tem que bater, voto a voto e turno a turno,
com o resultado oficial divulgado pelo TSE.

```
[oficial] totais de 2018 (1º turno) batem exatamente com o resultado divulgado
[oficial] totais de 2018 (2º turno) batem exatamente com o resultado divulgado
[oficial] totais de 2022 (1º turno) batem exatamente com o resultado divulgado
[oficial] totais de 2022 (2º turno) batem exatamente com o resultado divulgado
```

As guardas de [`qualidade/validacoes.py`](qualidade/validacoes.py) rodam dentro dos passos e
abortam a execução quando uma condição necessária não se sustenta:

| Guarda | O que assegura |
|---|---|
| `validar_totais_oficiais` | o total nacional de Presidente bate com o resultado divulgado, turno a turno |
| `checar_taxa_de_juncao` | um `merge` que casa menos linhas do que deveria não passa silenciosamente |
| `checar_soma_preservada` | agregar e realocar não cria nem destrói voto |
| `checar_chave_unica` | as colunas usadas como chave realmente identificam uma linha |
| `checar_codigo_municipio` | o código do município tem sempre 5 dígitos, em todos os anos |
| `checar_crs_metrico` | distância é medida em metros, nunca em graus |
| `checar_coordenadas_no_brasil` | latitude e longitude não estão trocadas |
| `checar_constante_por_grupo` | um agrupamento não herda atributo ambíguo |

Boa parte delas existe por causa de particularidades do dado bruto, que são descritas passo a passo
em [PIPELINE.md](PIPELINE.md): o código de município exportado sem zero à esquerda em alguns anos,
duas grafias de "voto em branco" convivendo, eleição suplementar publicada sob o mesmo ano, voto de
legenda com sequencial vazio em um estado. Cada uma tem teste automatizado.

Depois de publicar, o produto é conferido por fora: `qualidade/validar_publicado.py` abre os 16.717
arquivos num parser estrito, confere que toda região citada existe no município, que os percentuais
de Presidente fecham 100 e que as listas de regiões somam 1 e estão ordenadas. É a verificação que
pega o que as guardas de etapa não veem — um arquivo válido para o Python e ilegível para o
navegador, por exemplo.

Há ainda `qualidade/diagnostico_distancias.py`, que mede a distância entre cada endereço e o local
de votação atribuído. Não derruba nada: serve para achar locais com coordenada suspeita, que
continuam sendo "o mais próximo" de alguém mesmo estando a quilômetros dali.

## O que o dado cobre — e o que não cobre

Números da execução nacional, publicados em `publicado/cobertura.json`:

| | |
|---|---|
| Locais de votação com coordenada | **84,1%** |
| Votos dentro de alguma região | **87,4%** |
| Endereços atribuídos a uma região | **99,99%** |
| Acerto do índice, ponta a ponta | **96,40%** com rua, bairro e número; 92,44% com rua e número |
| Endereços sem número (S/N) | 23,8% |

### O que é "acerto"

O acerto mede **a consulta contra a coordenada**, e é assim que ele é apurado: sorteiam-se 134.981
endereços reais do CNEFE, que têm latitude e longitude. Para cada um há uma resposta de referência —
o local de votação mais próximo daquela coordenada, dentro do município — e uma resposta da
consulta, obtida como o usuário faria, digitando rua e, conforme o caso, bairro e número.

- **Acerto:** as duas respostas são o mesmo local de votação.
- **Erro:** a consulta devolve outro local. Na prática, o site mandaria a pessoa para um local de
  votação diferente do mais próximo da casa dela, e mostraria o resultado de lá.

O erro, portanto, é o preço da ambiguidade do texto: o índice conhece a rua, não a casa. Duas
pessoas na mesma rua, em pontas opostas, recebem a mesma resposta se não houver número nem bairro
para separá-las.

**Duas coisas que este número não mede.** Primeira: se a pessoa realmente vota naquele local — a
premissa "vota no mais próximo" é do projeto inteiro e está discutida em
[LIMITACOES.md](LIMITACOES.md). Segunda: os locais sem coordenada, que ficam fora da conta e
aparecem nas linhas de cobertura acima.

| O que o usuário informa | Acerto |
|---|---:|
| Só a rua | 78,29% |
| Rua + bairro | 87,61% |
| Rua + número | 92,44% |
| Rua + bairro + número | **96,40%** |

O erro não está espalhado: concentra-se em **endereço sem número**, que é 23,8% do país. Num sítio
da zona rural onde o CNEFE não registra número, todos os endereços da via recebem a mesma resposta —
e é por isso que o bairro vale mais que o número nos estados rurais (em Goiás, 89,5% contra 84,0%) e
menos nos urbanos (em São Paulo, 87,2% contra 97,5%).

Por isso a consulta devolve, junto com a região, um nível de confiança (`exata`, `interpolada`,
`provavel`) e a lista de locais possíveis com o peso de cada um — a resposta honesta às vezes é "é
provavelmente aqui, mas pode ser ali".

Detalhes em [LIMITACOES.md](LIMITACOES.md).

## Estado

Brasil completo e validado ponta a ponta: 27 estados, 5.563 municípios, 74.142 regiões. Próximo
passo: o site.

O resultado de **2026** entra quando o TSE publicar a votação por seção: é acrescentar o ano e o
total oficial no `config.py`. Os locais de votação de 2026 vêm do próprio arquivo de votação; depois
de medir quantos já têm coordenada (`qualidade/comparar_locais.py`), a malha passa a ter 2026 como
referência, com os votos de 2018 e 2022 realocados para ela. Passo a passo em
[PIPELINE.md](PIPELINE.md#mudando-o-recorte).

## Licença

MIT. Os dados de origem são públicos: TSE (dados abertos) e IBGE (CNEFE e Censo 2022).

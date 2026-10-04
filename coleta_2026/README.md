# Coleta dos resultados de 2026, direto do TSE

Como transformar a divulgação oficial da apuração em votos por **local de votação**,
que é a unidade do projeto [Como meus vizinhos votam](../README.md).

Este documento é escrito a partir do código desta pasta, e cresce a cada etapa pronta.

---

## 1. O problema: a granularidade que ninguém publica

Na noite da apuração, o TSE publica resultados em três granularidades: **Brasil**, **UF**,
**município** e **zona eleitoral**. Nenhuma delas serve aqui, porque a pergunta do projeto é
"como votou o local de votação mais próximo da sua casa" — e um local não é uma zona.

O resultado por local existe num lugar só: no **Boletim de Urna**, o arquivo que cada urna
emite ao fechar. Ele é publicado por seção eleitoral, e traz o código do prédio onde a urna
está. Somando os boletins de um prédio, chega-se ao que o projeto precisa.

São **499.248 seções principais** — contadas nos arquivos de configuração do próprio TSE,
em 01/10/2026, somando as 27 UFs e o exterior. O caminho é esse, e o custo também.

---

## 2. O servidor do TSE, e as regras para consumi-lo

Tudo vem de `https://resultados.tse.jus.br`, numa árvore de pastas descrita no
[guia de download](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-instrucoes-para-download-dos-arquivos-da-divulgacao-2026):

```
<ambiente>/<ciclo>/<eleição>/dados/<uf>/        resultados agregados (EA20)
<ambiente>/<ciclo>/arquivo-urna/<pleito>/
    config/<uf>/<uf>-p<pleito>-cs.json          todas as seções da UF (EA16)
    dados/<uf>/<município>/<zona>/<seção>/
        p...-aux.json                           hashes dos arquivos daquela urna (EA18)
        <hash>/<arquivo>-bu.dat                 o Boletim de Urna
```

**Nada disso pode ser adivinhado.** Da apresentação técnica do TSE, seção "Regras de consumo":

> Máximo de **100 requisições por IP por segundo**. Bloqueio de 10 minutos (renovado).
> Caso os endereços sejam requisitados de forma incorreta (**404**), também pode haver bloqueio.
> Não é possível listar os arquivos.

Duas consequências, que moldam o código inteiro:

1. **Limite de taxa obrigatório**, e não opcional. Em [`tse.py`](tse.py) ele está dentro do
   cliente, numa janela deslizante de um segundo compartilhada entre threads — quem chama não
   tem como esquecer de respeitá-lo. O projeto usa **60 req/s**, 60% do teto.
2. **Nenhuma URL inventada.** Todo caminho sai de um arquivo de configuração. Por isso o
   código pede primeiro o `ele-c.json`, depois a configuração de seções, e só então os
   arquivos de urna — exatamente a ordem que o guia recomenda.

### Descoberta: nada de código fixo

O código da eleição muda a cada pleito e **não** pode ser escrito no código-fonte. Ele vem do
arquivo de configuração de eleições:

```python
cliente = tse.Cliente()
eleicao = cliente.eleicao_federal(2026, turno=1)
# Eleicao(codigo='6257', nome='Eleição Ordinária Federal - 2026 1º Turno',
#         ciclo='ele2026', pleito='3220', data='04/10/2026')
```

Com isso montam-se os caminhos, com os sufixos no formato que o TSE exige (`e006257`,
`p003220` — seis dígitos, zeros à esquerda).

### Seções agregadas não têm urna

O arquivo de configuração de seções marca, em `nsp`, que uma seção foi **agregada** a outra:
os eleitores dela votam na seção principal. Pedir o arquivo de urna de uma agregada daria 404
— e 404 arrisca bloqueio. Por isso `secoes_da_uf()` descarta as agregadas.

---

## 3. Ler o Boletim de Urna

O BU é ASN.1 em **DER**, um formato binário. Não há schema público junto dos arquivos, então
o parser foi construído em duas camadas.

### 3.1 Um leitor de DER em 80 linhas ([`der.py`](der.py))

DER é recursivo e simples: cada nó é `[etiqueta][tamanho][conteúdo]`, e um nó "construído"
contém outros nós. `der.ler()` devolve a árvore; `der.desenhar()` imprime, que foi como a
estrutura abaixo foi descoberta.

### 3.2 A estrutura do BU ([`bu.py`](bu.py))

O arquivo é um **envelope** com o boletim dentro:

```
envelope
  ├── cabeçalho: data/hora, pleito
  ├── identificação da seção: município, zona, LOCAL, seção     ← a chave do projeto
  └── octet string ──► boletim
                         ├── identificação da seção (de novo)
                         ├── comparecimento
                         └── resultados por eleição
                               └── por cargo (1 presidente, 6 deputado federal…)
                                     └── votáveis
```

Cada votável é uma tupla `{tipo, quantidade, {partido, número}}`, e o tipo é o que casa com o
vocabulário do nosso pipeline:

| código | significado | identificação |
|---|---|---|
| 1 | nominal | partido e número do candidato |
| 2 | branco | — |
| 3 | nulo | — |
| 4 | legenda | partido, com número igual ao partido |

O campo decisivo é o **local**: é ele que diz em que prédio aquela urna estava. Sem ele, o
BU seria apenas mais um dado por seção, e o projeto não existiria.

```python
boletim = bu.ler(open("...-bu.dat", "rb").read())
# Boletim(municipio='00310', zona=17, local=1074, secao=1, pleito=452, …)
```

### 3.3 A conferência que o próprio BU permite

`bu.conferir()` aplica a regra que não pode falhar: **a soma dos votos de um cargo é igual ao
comparecimento** daquela urna. Quem aparece vota uma vez por cargo — em alguém, em branco ou
nulo. Se a soma não fecha, o parser leu errado, e é melhor saber antes de publicar.

### 3.4 Recusar é diferente de estourar

Este parser lê por **posição** na árvore DER. Se o formato de 2026 vier diferente do de 2024,
indexar `filhos[8]` devolve outra coisa — e o pior resultado possível seria somar o campo errado
e publicar. Então `bu.ler` valida o que encontrou e levanta `BoletimInvalido` com o nome do campo
que não encaixou:

```
município e zona: esperava um nó com filhos
resultados por eleição: esperava ao menos 9 campo(s), veio 8
cargo sem nenhum votável
```

E `ler_bus` trata recusa como estatística, não como exceção: conta, guarda os cinco primeiros
motivos e segue. O que interrompe a conversão é a **taxa**:

| guarda | limite |
|---|---|
| recusas no acervo todo | 0,5% (`LIMITE_RECUSA`) |
| recusas nos 200 primeiros boletins | 50% (`AMOSTRA_INICIAL`) |

A segunda é curto-circuito: não vale varrer meio milhão de arquivos para descobrir no fim que o
formato mudou. Uma urna com arquivo corrompido é recusa isolada e passa; formato diferente é
recusa em massa e para tudo, porque seguir seria publicar um resultado construído sobre leitura
errada.

### 3.5 O que a especificação oficial exige

O TSE publica a especificação do BU em ASN.1, com exemplos e um leitor em Python
([documentação técnica do software da urna](https://www.tse.jus.br/eleicoes/eleicoes-2022/documentacao-tecnica-do-software-da-urna-eletronica)).
Três coisas dela moldam este parser.

**1. O formato muda entre eleições.** A especificação publicada é de 2022. O **leitor oficial do
TSE**, com ela, falha sobre um boletim real de **2024**:

```
EntidadeBoletimUrna.urna.correspondenciaResultado.carga.dataHoraCarga:
Expected DataHoraJE with tag '1b', but got '30'
```

E `ResultadoVotacaoPorEleicao` passou de **3 campos em 2022 para 7 em 2024**. O formato muda de
uma eleição para a outra, com aviso na especificação, e leitura por posição fixa quebra.

Daí a leitura ser tolerante: **tenta a posição conhecida e, se o campo não tiver a forma
esperada, procura entre os outros**. A forma é reconhecida pelo que não muda — um votável é
um nó com tipo e quantidade em etiquetas de contexto. Se nenhum campo tiver a forma, é recusa, não
palpite.

Com isso o mesmo código lê **os dois formatos**: os 1.355 boletins de 2024 e os quatro exemplos
de 2022 que vêm no pacote do TSE, que trazem a estrutura que 2026 terá — **duas eleições no mesmo
arquivo, cinco cargos** (presidente, governador, senador, deputado federal e estadual). Os quatro
fecham a própria conta.

**2. São cinco tipos de voto, não quatro.** `TipoVoto` inclui `cargoSemCandidato (5)`, para o
cargo que não teve candidatura nenhuma. Ele não aparece em eleição geral para presidente ou
deputado federal, mas, se aparecesse sem estar previsto, a conferência acusaria "tipo
desconhecido" e **descartaria a urna inteira**.

**3. O boletim diz em que fase foi gerado.** `Fase` distingue `simulado (1)`, `oficial (2)` e
`treinamento (3)`, e a urna gera os três com a mesma estrutura. Por isso a conversão e a camada ao
vivo **só contam boletim de fase oficial**: um arquivo de treinamento que apareça na árvore oficial
é recusado em vez de virar voto.

Dela também saem os códigos de cargo que o projeto usa — 1 presidente, 6 deputado federal,
11 prefeito, 13 vereador.

---

## 4. A prova: comparar com o resultado oficial

Conferência interna não basta: um parser pode somar certo e ler o candidato errado. A prova
de verdade é externa. Pegamos **todas as 65 seções** da zona 17 de Alta Floresta d'Oeste (RO)
na eleição de 2024, somamos os BUs e comparamos com o arquivo de resultado que o próprio TSE
publica para aquela zona:

| | TSE | somando os BUs |
|---|---:|---:|
| GIOVAN DAMO (44) | 10.318 | **10.318** |
| OSMAR BENITES (22) | 2.514 | **2.514** |
| ANTONIO BARBOSA (13) | 665 | **665** |
| brancos | 191 | **191** |
| nulos | 263 | **263** |
| comparecimento | 13.951 | **13.951** |

Todos os números, exatos. É isso que autoriza seguir para a coleta nacional.

---

## 5. Baixar meio milhão de boletins ([`coletar.py`](coletar.py))

Quatro decisões moldam o coletor:

- **Um zip por UF**, não meio milhão de arquivos soltos. O sistema de arquivos sofre com
  centenas de milhares de arquivos de 5 KB, e o zip ainda serve de registro do que já foi
  baixado: retomar é ler a lista de nomes de dentro dele.
- **Guardar o BU cru**, não o resultado já interpretado. Se o parser precisar de conserto no
  meio da noite, conserta-se e reprocessa, sem baixar de novo.
- **Só o arquivo `bu`**, dos quatro que cada urna publica. O RDV e o log não entram na conta
  do projeto, e cada arquivo a mais seria meio milhão de requisições.
- **Nada é pedido antes de existir** — é o item 5.1, e é o que torna a noite tranquila.

Medido num ensaio com Roraima em 2024: **1.355 BUs em 1,8 min**, a 12 BU/s com o limitador em
25 req/s — exatamente o esperado, já que cada BU custa duas requisições (o auxiliar, que diz
onde o arquivo está, e o arquivo). A 60 req/s, as 499 mil seções levam cerca de **4h40**.

Rodar de novo o mesmo comando não baixa nada: tudo já está no zip.

### 5.1 O TSE diz quais seções já transmitiram

O arquivo de configuração de seções (EA16) traz, em cada seção, dois campos que parecem
burocráticos e resolvem o problema inteiro:

> **da** — Data do arquivo auxiliar da seção. Disponível somente para a seção principal e
> **após a geração do arquivo auxiliar correspondente**.

Ou seja: o próprio arquivo de configuração é o índice do que já existe. Verificado nos dois
extremos:

| | seções | principais | com `da` preenchido |
|---|---:|---:|---:|
| RR, 2024, eleição encerrada | 1.511 | 1.355 | **1.355** |
| RR, 2026, antes da votação | 1.627 | 1.519 | **0** |

As 1.355 são exatamente os BUs que o ensaio baixou, e os horários de `da` vão de 17:09 a 20:19
daquele domingo — o arquivo é refeito conforme as urnas transmitem.

Com isso o coletor não precisa adivinhar nem tentar: em cada passada ele lê a configuração de
novo e pede **só** as seções que já têm arquivo auxiliar. Zero 404 — que é justamente o que o
TSE avisa que pode levar a bloqueio.

### 5.2 Passadas repetidas em vez de uma tentativa só

```bash
python coleta_2026/coletar.py --passadas 8 --espera 20
```

Cada passada baixa o que apareceu desde a anterior; o que já está no zip não é pedido de novo.
O laço para sozinho quando todas as seções principais estão no zip, e o relatório de cada
passada diz quanto falta e quantas seções ainda não transmitiram:

```
  RR: 1.519 seções | 1.213 com urna publicada | 1.100 já baixadas | 113 a baixar | 306 ainda sem urna
```

Seção que anuncia BU no auxiliar mas não entrega o arquivo fica em `{UF}-pendentes.json`, e a
passada seguinte tenta de novo.

### 5.3 Qual transmissão vale

O auxiliar de cada seção traz uma lista de hashes, não um só, e o EA18 dá situação a cada um:
Recebido, Rejeitado, Excluído, **Totalizado**. O arquivo "poderá ser atualizado após cada
totalização final ou retransmissão de arquivos de urna".

Então pegar o último da lista está errado quando a seção retransmitiu: traria o arquivo
rejeitado. O coletor pega o hash **Totalizado**, e só cai no último recebido quando nenhum
está totalizado ainda — que é a situação durante a apuração.

Nas 150 seções de Roraima que amostrei em 2024, todas tinham um hash só, `Totalizado`, e seção
`Totalizada`: o caso da retransmissão é raro. Mas raro em 499 mil seções não é zero, e o custo
de tratar é este parágrafo de código.

Daí uma consequência prática para o horário: **BU baixado antes da totalização da seção pode
não ser o definitivo**. Cada passada informa quantas seções estavam nessa situação:

```
  1.284 seção(ões) baixadas antes da totalização final — se retransmitirem, o BU que vale muda
```

Começar a coleta depois dos 100% apurados elimina isso por construção. Começar antes adianta
umas duas horas de parede e deixa esse resíduo — que a conferência da seção 8 pega se for
grande, e que é desprezível se for a ordem de grandeza de 2024.

### 5.4 O exterior entra na coleta

O TSE trata o exterior como uma abrangência a mais (`zz`): 1.351 seções principais, só para
presidente. Elas **não** aparecem no site — não há endereço do CNEFE para casar, e o passo 22
descarta locais que não estão na malha. Mas entram no total do Brasil que a conferência
compara: sem elas faltariam uns 300 mil votos e a guarda barraria a publicação, com razão.

## 6. Dos boletins para a tabela do pipeline ([`converter.py`](converter.py))

O passo 22 do pipeline espera uma tabela com uma linha por local, cargo e votável. O conversor
produz exatamente esse formato — e é onde moram as três traduções entre o BU e o pipeline.

### 6.1 O número não identifica o candidato

O BU registra o **número digitado**, não o candidato. Para achar nome e partido é preciso o
cadastro de candidatos, e a chave certa não é o número sozinho, nem número + UF: é número +
cargo + **unidade eleitoral** (`SG_UE` no cadastro), que vale:

| cargo | unidade eleitoral |
|---|---|
| presidente | `BR` — o número é nacional |
| governador, senador, deputado federal e estadual | a UF |
| prefeito e vereador | o **município** |

Isto foi descoberto errando: ligando por número + UF, o nº 15 em Boa Vista virou "ANTÔNIO
REIS", que é o candidato de mesmo número em Caracaraí. A contagem estava certa e o nome,
errado — o pior tipo de erro, porque não quebra nada.

### 6.2 Um número pode ter duas candidaturas

Em substituição de candidato, o cadastro guarda as duas inscrições com o mesmo número. No
ensaio, o nº 44 em Boa Vista tinha NICOLETTI (anulada) e CATARINA GUERRA (que concorreu e
recebeu os 40.410 votos). `escolher_candidatura()` resolve por ordem de preferência —
situação de totalização real, depois candidatura apta, depois inscrição mais recente — e
**informa quantos casos resolveu**, em vez de escolher em silêncio.

### 6.3 O BU e a totalização discordam, por desenho

A urna não sabe que uma candidatura foi anulada: grava o voto como nominal, no número que foi
digitado. Quem reclassifica é a totalização. O arquivo oficial publica cada parcela separada,
e por isso a diferença é **mensurável, não misteriosa**:

```
tv  = vvc + vb + tvn            total de votos = válidos computados + brancos + nulos
vvc = vnom + vl + van + vansj   válidos = nominais + legenda + anulados + anulados sub judice
tvn = vn + vnt                  nulos = nulos de urna + nominais que a totalização anulou
```

Dois casos reais do ensaio de 2024, os dois em Roraima:

| município | o que o oficial diz | o que o BU diz |
|---|---|---|
| São João da Baliza | nº 15 com 836 votos na lista de candidatos, e um `vnom` de 4.009 que **não** os inclui — eles estão em `vansj` | 4.845 nominais, 836 deles no nº 15 |
| Amajari | `vnom` 6.405, `vnt` 1 (um nominal que virou nulo) | 6.406 nominais |

A escolha é **manter o que a urna registrou**: é o dado primário, e o projeto mostra o que foi
digitado naquele prédio. Quando o CSV oficial sair, ele entra no lugar com a classificação
final — ver o fim da seção 10.

Uma dúvida que isto levanta: **aparece voto em número que não existe?** Não, e por um motivo
que está na urna, não aqui — ela só aceita número de candidatura carregada; qualquer outro ela
anuncia como nulo, e o BU já grava nulo. O que aparece é o caso de cima: candidatura que
estava na urna no domingo e que a totalização depois não contou. Essas têm nome e partido no
cadastro, então o conversor as nomeia normalmente — no ensaio de Roraima, 350 mil votos entre
prefeito e vereador, **zero linha sem nome**. Se um número vier sem cadastro, o conversor
avisa (`[aviso] N linha(s) sem nome no cadastro`) em vez de publicar um nome vazio.

### 6.4 O local que o boletim declara, e o que a malha conhece

A junção do passo 22 é por `UF_município_zona_local`, e o local vem do boletim.
Voto em local que a malha não conhece é descartado ali — então vale medir antes de
três horas de pipeline. O conversor avisa:

```
[aviso] 10.019 voto(s) (3,14%) em 14 local(is) fora da malha — o passo 22 vai descartá-los
```

Esses 3,14% são do ensaio, e enganam: os boletins são de 2024 e a malha é feita de
2018, 2022 e 2026 — nenhum local que nasceu e morreu em 2024 está nela. **A
exposição real de 2026 é 0,32%**: dos 95.116 locais com eleitorado em 2026, 701
não estão na malha, 682 deles por não ter coordenada utilizável em nenhuma das
duas fontes. É a mesma perda que o projeto já tem com 2018 e 2022, não uma nova.

**O voto perdido não é remendado, e isso é decisão.** O caminho óbvio seria usar
o local que o cadastro de locais atribui àquela seção, mas ele não resolve e não é
confiável: medido no ensaio de Roraima, cadastro e boletim **discordam do local em
6,5% das seções** (88 de 1.355), e nos 44 casos de perda o cadastro apontava para o
mesmo local que o boletim. Mover voto para outro prédio com base numa fonte que
discorda da urna faria o resultado parecer completo sem ser — pior que perder 0,3%
contado.

### 6.5 A prova, de novo

Convertidos os 1.355 BUs de Roraima e comparados município a município com os arquivos
oficiais do TSE:

| município | nosso | oficial | candidatos idênticos |
|---|---:|---:|---|
| Boa Vista | 183.911 | 183.911 | 4/4 |
| Caracaraí | 12.486 | 12.486 | 4/4 |
| Rorainópolis | 14.759 | 14.759 | 4/4 |
| …todos os 15 | sempre igual | | sempre igual |

Prefeito e vereador, 15 municípios, comparecimento, válidos, brancos, nulos e **todos** os
votáveis — candidato por candidato e legenda por partido: zero de diferença. Os dois casos do
item 6.3 fecham quando a conta do oficial é lida inteira, como a seção 8 explica.

---

## 7. Entregar no formato que o pipeline já lê

Esta é a parte que **não** exige mudar o pipeline, e isso é de propósito.

O passo 22 lê `dados/intermediario/votos_local_votacao.parquet`. O conversor grava exatamente
nesse arquivo, preservando os anos que já estavam lá e substituindo o ano convertido se ele já
tiver sido gravado antes. Nenhum passo precisa saber que 2026 veio por outro caminho:

```
votos_local_votacao.parquet   ← passo 21 (CSV do TSE: 2018, 2022)
                              ← converter.py (BUs: 2026)
        │
        ▼
      passo 22 ──► passo 31 ──► passo 32 ──► passo 40 ──► site
```

Antes de mesclar, o conversor compara o esquema coluna a coluna e **recusa** gravar se
divergir — mesclar formato errado corromperia a base de 2018 e 2022 junto.

Uma armadilha documentada no código: rodar o passo 21 **depois** reescreve o arquivo a partir
dos CSVs e leva 2026 junto. A saída própria do conversor
(`votos_local_votacao_2026.parquet`) continua no lugar, então basta rodar o conversor de novo.

### A única linha de configuração que muda

`config.ANO_REFERENCIA_MALHA = 2026`, e só no dia. Ela define quais regiões estão **ativas**
— as que tiveram voto naquele ano —, e as inativas são realocadas para a ativa mais próxima.
Sem a troca, os 7.298 locais que só existem em 2026 ficariam inativos e os votos deles seriam
somados ao vizinho.

`ANOS_ELEICAO` **não** muda: essa lista diz ao passo 21 quais CSVs do TSE ler, e 2026 não tem
CSV — acrescentá-la faria o passo 21 falhar procurando um arquivo que não existe.

---

## 8. A guarda que autoriza a publicação ([`conferir.py`](conferir.py))

As provas das seções 4 e 6.5 foram feitas à mão, em Roraima. No domingo isso precisa virar um
comando com duas respostas possíveis: **pode publicar** ou **não pode**. É o que este script
faz — e ele sai com código 1 na divergência, para poder ser o porteiro antes do deploy.

```bash
python coleta_2026/conferir.py --eleicao 6257 --cargo 1              # presidente, Brasil
python coleta_2026/conferir.py --eleicao 6257 --cargo 6 --nivel uf   # dep. federal, por UF
```

### 8.1 A comparação é exata, não tolerante

Comparar o nosso total de nominais com o `vnom` do arquivo oficial não funciona: os dois contam
coisas diferentes, e a diferença chega a **836 votos num único município** — tamanho que nenhuma
tolerância razoável pegaria sem também engolir erro de verdade.

Lendo a conta do oficial inteira (seção 6.3), as duas somas passam a ser a mesma coisa:

| o nosso | o do oficial |
|---|---|
| nominais + legenda | `vvc + vnt` |
| brancos | `vb` |
| nulos | `tvn - vnt` |
| soma de tudo | `e.c` (comparecimento) |

Com isso a comparação é **exata**: 15 municípios, dois cargos, zero de diferença em todas as
linhas. E se a identidade `tv = vvc + vb + tvn` não fechar no arquivo do TSE, o script diz
isso em vez de comparar — porque aí é o nosso entendimento do formato que mudou.

Sobrou uma única folga, e ela tem motivo: **seção que não transmitiu o BU**. Nesse caso faltam
votos do nosso lado em todas as prateleiras ao mesmo tempo, e o script reconhece o padrão,
mostra o tamanho do buraco e só aprova se ele couber em `TOLERANCIA_FALTA` (0,05% do
comparecimento).

### 8.2 Um porteiro que só sabe dizer "sim" não serve

Guarda que nunca barra nada não é guarda. As duas formas de erro que importam foram testadas
com dados sabotados de propósito, a partir do ensaio de 2024:

| sabotagem | o que a guarda fez |
|---|---|
| apagar um local de votação inteiro de Boa Vista (2.148 votos) | barrou: *faltam 2.148 votos (1,1680%), acima do limite de 0,05%* |
| mover 300 votos de um candidato para outro, deixando o comparecimento intacto | barrou: *nº 15 +300*, *nº 43 −300* |

A segunda é a que justifica comparar candidato por candidato: os quatro totais continuavam
perfeitos. Só a lista nominal acusa.

### 8.3 Testes

O código desta pasta tem 94 testes em [`tests/test_coleta_2026.py`](../tests/test_coleta_2026.py),
que rodam junto com o resto do projeto (`python -m pytest tests -q`, 226 no total). Dois tipos
convivem ali de propósito:

- **Bytes fabricados.** A função `boletim()` monta um BU completo em DER, campo por campo — é
  documentação executável do formato que `bu.py` percorre por posição. Com ela dá para testar
  o que não se acha num arquivo real: comparecimento que não fecha, tipo de voto desconhecido,
  arquivo que não é BU.
- **Um BU real.** [`tests/fixtures/bu_exemplo_2024.dat`](../tests/fixtures/bu_exemplo_2024.dat) é o
  boletim publicado da seção 1, zona 17, local 1074 de Alta Floresta/RO — por coincidência o
  mesmo local que já servia de exemplo nos testes do passo 10. O teste fabricado prova que a
  regra está certa; o arquivo real prova que a regra serve para o que o TSE publica.

Os casos de reclassificação da seção 6.3 estão fixados como teste com os números reais de São
João da Baliza e Amajari, para que a conta da seção 8.1 não possa ser desfeita sem alguém
notar.

---

## 9. A camada ao vivo: 2026 no site durante a apuração

Tudo acima descreve **uma** publicação, de madrugada, depois de coletar meio
milhão de boletins. Mas o resultado interessa às 17h30, não às 2h. E entre uma
coisa e outra há 4h40 de coleta, 1h30 de pipeline e 2h de deploy — nenhuma delas
encurtável.

A saída não foi encurtar: foi tirar o site do caminho. Durante a apuração, **o
navegador de quem consulta busca os boletins do próprio local, direto no servidor
do TSE**, e o site mostra o resultado sem que nada disso passe pelo pipeline nem
pelo nosso servidor.

### 9.1 Por que isso é possível (e foi medido antes de ser escrito)

| | medido |
|---|---|
| CORS | o TSE devolve `Access-Control-Allow-Origin` **refletindo o nosso domínio**, inclusive no binário do boletim |
| cache | `max-age` de 17 a 58 s nos arquivos vivos, 3.600 s no boletim (que é imutável, endereçado por hash) |
| limite | 100 requisições por segundo **por IP** — cada visitante gasta o próprio orçamento, não o nosso |
| requisições por consulta | mediana **9**, p90 25, pior caso 145 (2 por seção: o auxiliar e o boletim) |
| tempo medido | **300 ms** para 13 urnas, com 12 requisições em paralelo |

O limite por IP é o detalhe que decide a arquitetura. Um proxy no nosso servidor
concentraria tudo num IP só, e um bloqueio de 10 minutos derrubaria o recurso
para todo mundo ao mesmo tempo, justamente no pico. Do lado do cliente, o risco
se dissolve.

### 9.2 O que precisou ser publicado ([`ao_vivo.py`](ao_vivo.py))

O TSE indexa o arquivo de urna por município/zona/seção. A pergunta do site é por
**região** — o grupo de locais na mesma coordenada. Essa ponte só nós sabemos
fazer, e ela vai em três arquivos estáticos, gerados antes do dia:

```
publicado/ao_vivo/
  config.json              parâmetros da eleição e o interruptor
  secoes/{ibge}.json       região → [[zona, local, seção], …] e o código TSE
  candidatos/presidente.json, {UF}.json     número → nome e partido
```

Nenhum deles tem resultado. São mapa, cadastro e configuração — o resultado vem
do TSE, na hora.

### 9.3 O que a tela diz, e por que nessas palavras

O contador é **"9 de 13 urnas processadas"** — e a escolha da palavra é deliberada.
"Processadas" descreve o que foi feito deste lado. Ele cobre com honestidade as
três situações que levam uma urna a não entrar na conta:

1. a urna ainda não transmitiu;
2. o boletim chegou e foi recusado — a soma dos votos não bate com o
   comparecimento que a própria urna declara, o que indica leitura errada nossa;
3. o boletim chegou e declara pertencer a outro local.

As três têm a mesma consequência para quem consulta: aquela urna não está no
número exibido. A distinção entre elas é técnica, serve a quem opera, e fica nos
contadores — não numa tarja que alguém lê no celular, fora de contexto, e entende
como acusação a quem apurou.

O que a tela **nunca** faz é mostrar voto que não foi conferido, ou dizer
"concluída" faltando urna: o estado só vira concluído quando processadas e
previstas se igualam.

### 9.4 As quatro guardas

A camada publica número de eleição na tela sem ninguém olhando antes. Então ela
desconfia de si mesma em quatro pontos:

1. **Não pedir o que não existe.** Seção que ainda não transmitiu dá 404, e 404
   pode bloquear — aqui, o IP do visitante. O módulo lê antes a configuração de
   seções da UF, onde o campo `da` só aparece depois que o arquivo auxiliar foi
   gerado. Zero 404 por construção. Custo: 446 KB comprimidos no pior caso (SP),
   uma vez por sessão.
2. **O boletim manda sobre o nosso mapa.** O mapa diz o que pedir; o local de
   verdade é o que o boletim declara. Boletim que diz pertencer a outro local é
   descartado desta região — e isso importa, porque o cadastro de locais discorda
   da urna em 6,5% das seções (medido em 2024, seção 6.4).
3. **Boletim que não fecha não entra.** A soma dos votos de um cargo tem de ser o
   comparecimento daquela urna; se não for, a urna inteira é descartada.
4. **Ninguém pode ter mais voto aqui do que no município.** O agregado oficial do
   município custa uma requisição e dá o teto por candidato. Um parser inflando
   número é pego por essa desigualdade.

E a regra que vale sobre todas: **qualquer falha devolve a página de ontem.** Sem
bloco de 2026, sem erro na tela. O freio de mão é apagar `publicado/ao_vivo/` do
servidor — sem republicar nada, sem tocar no pipeline.

### 9.5 Como isso foi testado sem existir boletim de 2026

Não há como ler um boletim de 2026 antes do dia: o servidor do TSE só mantém os
ciclos correntes (2022 responde `NoSuchKey`) e o ambiente de simulado esteve indisponível
durante toda a preparação, em 01 e 02/10/2026.
O que dá para fazer é testar tudo o mais, com dado real de 2024:

| teste | o que prova |
|---|---|
| [`teste_bu_js.py`](teste_bu_js.py) | o parser do navegador e o do Python produzem a **mesma forma canônica** em **1.358 boletins** — os 1.355 de Roraima mais os exemplos oficiais do TSE, que são de outro formato — e recusam os mesmos 6 arquivos estragados |
| [`teste_ao_vivo_js.py`](teste_ao_vivo_js.py) | a camada busca no TSE de verdade e chega ao mesmo número que o Python, em 3 regiões (13, 11 e 1 urnas) |
| `tests/test_ao_vivo.py` | o mapa de região → seções, com seção agregada fora e dois locais do mesmo prédio juntos |
| prints em 1280 e 390 px | a tela não quebra, e o seletor de ano ganha o ano novo |

O teste de tela usa um disfarce que vale explicar: os bytes são de 2024, de uma
eleição municipal, mas a configuração do ensaio chama o cargo de "presidente".
Assim o caminho exercitado é exatamente o de 2026 — seletor de ano, abas, aviso,
compartilhamento —, com boletins que existem de verdade.

### 9.6 O que a camada ao vivo **não** faz

Ela não substitui a coleta. A publicação definitiva precisa de resultado para as
90.898 regiões, não só para as que alguém consultou — então os boletins continuam
sendo baixados, convertidos e conferidos durante a noite, e de madrugada o
`publicado/` é refeito com tudo. Quando isso acontece, a camada se desliga
sozinha: ela só completa ano que falta no arquivo publicado.

E ela não alcança a **imagem de prévia** do link compartilhado, que o `card.php`
monta no servidor a partir de `publicado/`. Durante a noite, o cartão mostra a
eleição anterior enquanto a página mostra 2026. Por isso o texto que acompanha o
link leva os números — ele é montado no navegador, onde o dado ao vivo está:

```
Como votou AMERICA SARMENTO RIBEIRO E.E., em BOA VISTA – RR:
Arthur Henrique 75,6%, Catarina Guerra 23,2% — parcial, 9 de 13 urnas processadas
https://comomeusvizinhosvotam.com.br/l/1400100/5-1503
```

---

## 10. O processo inteiro, de ponta a ponta

Legenda: `█` é código novo desta pasta; o resto é o pipeline que já existia.

```
══ 1. MALHA ─ já está pronta; NÃO roda no domingo ═══════════════════════════

  dados_importados/                      TSE, dados abertos
  locais_votacao_2018_2022.parquet       eleitorado_local_votacao_{2018,2022,2026}
  (CNEFE, 78.568 locais com coordenada)  (107.190 locais: nome, endereço, coordenada,
            │                             eleitorado por seção)
            │                                        │
            │                                    passo 10
            │                                        │
            │                        locais_oficiais + eleitorado_local
            │                                        │
            └──────────────────┬─────────────────────┘
                               ▼
                           passo 11          coordenada do CNEFE manda;
                               │             a oficial preenche os buracos
                               ▼
              dim_regiao (97.981 regiões)  ·  de_para_local_regiao


══ 2. VOTOS ─ é aqui que entra o novo ═══════════════════════════════════════

   CSV do TSE                  ║   Divulgação em tempo real (2026)
   votacao_secao_{2018,2022}   ║
            │                  ║   ele-c.json ──► qual é a eleição (6257/3220)
        passo 21               ║        │
            │                  ║   cs.json    ──► as 499.248 seções        █
            │                  ║        │         (e quais já transmitiram)
            │                  ║        │
            │                  ║   aux.json   ──► onde está o BU de cada uma █
            │                  ║        │
            │                  ║   bu.dat     ──► o voto, por seção e LOCAL  █
            │                  ║        │
            │                  ║   █ coletar.py ──► dados/bruto/bu/{UF}.zip
            │                  ║        │            (3,3 GB, retomável)
            │                  ║        ▼
            │                  ║   █ converter.py ◄── consulta_cand_2026
            │                  ║        │              (nome e partido por número)
            │                  ║        ├──► votos_local_votacao_2026.parquet
            │                  ║        │
            │                  ║   █ conferir.py ──► soma o nosso e compara com o
            │                  ║        │            oficial do TSE: passa ou barra
            ▼                  ║        ▼
      votos_local_votacao.parquet  ◄─────┘
      2018 + 2022 + 2026 empilhados, mesmo esquema
                     │
                 passo 22      ANO_REFERENCIA_MALHA = 2026 define o que é "ativo";
                     │         região sem voto em 2026 é realocada para a vizinha
                     ▼
            votos_regiao.parquet


══ 3. ENDEREÇOS ─ a parte cara, ~2h ═════════════════════════════════════════

   CNEFE (111 milhões de endereços)
                     │
                 passo 31      cada endereço recebe a região ativa mais próxima
                     │         do mesmo município
                     ▼
         enderecos_regiao/2026/{UF}
                     │
                 passo 32      vira índice de ruas: trechos, dominante por rua,
                     │         listas por bairro
                     ▼
         indice_ruas · ruas_dominante · bairros_regioes


══ 4. PUBLICAÇÃO ═══════════════════════════════════════════════════════════

                 passo 40      JSON por município: resultados, eleitorado,
                     │         abstenção, chave pública do link, agregados
                     ▼
              publicado/  ──►  validações (pub, cob, dist, coord)
                     │
                     ▼
              site/deploy/deploy.py  ──►  comomeusvizinhosvotam.com.br
```

### Quem roda no domingo, e em que ordem

A linha do tempo de 2024 em Roraima dá a referência: o primeiro arquivo auxiliar apareceu às
**17:09** e o último às **20:19**. Numa eleição federal o rastro vai até mais tarde, porque as
urnas fecham às 17h **locais** e o Acre está duas horas atrás de Brasília.

| horário | o que acontece | o site mostra |
|---|---|---|
| sábado | malha de 2026 publicada, com os estáticos da camada ao vivo | histórico, com os locais de 2026 |
| domingo, a partir das 17h | as urnas começam a transmitir | **2026 ao vivo**, buscado no TSE pelo navegador |
| a partir de ~20h | o TSE fecha a apuração | 2026 ao vivo, já completo em quase todo local |
| 20h → ~00:40 | coleta dos boletins, em segundo plano | o mesmo |
| ~00:40 → ~01:00 | conversão e conferência | o mesmo |
| ~01:00 → ~02:30 | pipeline, do passo 22 em diante | o mesmo |
| ~02:30 → ~04:30 | deploy | **2026 definitivo**, e a camada ao vivo se desliga sozinha |

A camada ao vivo (seção 9) tira o site do caminho crítico: o resultado aparece às
17h30 sem depender de nenhuma das etapas abaixo. O que a noite faz é trocar o
parcial pelo definitivo, com a conferência exata no meio.

E o passo 31 não roda, porque a malha não muda — foi trocada no sábado. São 50
minutos a menos na madrugada.

São dois comandos: um à noite e um na manhã seguinte.

```bash
python -u coleta_2026/domingo.py      # tudo, menos publicar
python site/deploy/deploy.py          # depois de olhar o relatório
```

[`domingo.py`](domingo.py) existe por causa do horário, não da complexidade: cada etapa já
funcionava sozinha, mas entre o fim da coleta (~00:40) e o início do pipeline não pode haver
ninguém esperando para digitar a etapa seguinte. O que ele encadeia:

| # | etapa | duração |
|---|---|---|
| 1 | `coletar.py --passadas 8 --espera 20` | ~4h40 |
| 2 | `converter.py --eleicao <descoberto no TSE>` | ~15 min |
| 3 | confere que o ano entrou mesmo na tabela do pipeline | — |
| 4 | `conferir.py --cargo 1` (presidente, Brasil) | ~1 min |
| 5 | `conferir.py --cargo 6 --nivel uf` (dep. federal) | ~2 min |
| 6 | `config.py`: `ANO_REFERENCIA_MALHA = 2026` | já feito no sábado |
| 7 | `rodar_pipeline.py --so 22 40 pub qa cob dist coord` | ~1h30 |
| | **para aqui** — o deploy é decisão de gente | |

Três coisas que o desenho garante:

- **A ordem é a trava.** As conferências vêm antes de qualquer etapa que mude estado. Se uma
  delas sair com código 1, o script para: o `ANO_REFERENCIA_MALHA` não é tocado, o pipeline
  não roda, nada é publicado, e o log diz o que divergiu. Melhor acordar com o site do jeito
  que estava do que com ele errado.
- **Retomar é seguro.** `--de pipeline` (ou `coleta`, `conversao`, `conferencia`, `referencia`)
  recomeça de uma etapa. Nenhuma delas estraga o que já foi feito: a coleta retoma pelo zip, a
  conversão substitui as linhas do ano, a conferência só lê, a troca do ano é idempotente.
- **O código da eleição não está escrito em lugar nenhum.** Sai do `ele-c.json` no começo da
  noite, e se nem isso responder o script para antes de começar.

Para ensaiar a cadeia inteira em miniatura: `--uf RR` passa o recorte para a coleta e a
conversão.

A coleta pode ser adiantada para as 18h sem risco de bloqueio, porque o coletor só pede o que
já existe (seção 5.1): a primeira passada pega o que estiver transmitido, as seguintes pegam o
resto, e tudo fecha umas duas horas mais cedo. O que se paga por isso está na seção 5.3 — BU
baixado antes da totalização da seção pode ser substituído depois. **A conferência tem de rodar
depois dos 100% de qualquer jeito**, porque é contra o arquivo oficial que ela compara, e
arquivo parcial compara com apuração parcial.

Os passos **10, 11 e 21 do pipeline não rodam**. Os dois primeiros porque a malha já está
pronta; o terceiro porque ele reconstrói a tabela de votos a partir dos CSVs e levaria 2026
junto.

### Quando sair a base oficial por seção

Semanas depois, o TSE publica o CSV definitivo — com a classificação final, inclusive os votos
que a urna registrou como nominais e a totalização considerou nulos. Aí o caminho volta ao
normal do projeto: baixar o zip, acrescentar 2026 em `ANOS_ELEICAO`, rodar `--de 21`. O
conversor sai de cena, e **não deve ser rodado de novo para 2026**: ele substituiria as linhas
oficiais pelas dos boletins.

---

## 11. O que ainda falta

- [x] **Coletor** — feito e ensaiado com Roraima em 2024.
- [x] **Conversão** — feita e conferida contra os 15 municípios de Roraima, em dois cargos.
- [x] **Entrega no formato do pipeline** — o conversor grava na tabela que o passo 22 lê.
- [x] **Conferência** — `conferir.py`, exata contra o arquivo oficial em qualquer nível
      (Brasil, UF, município), e testada com dados sabotados para garantir que barra.
- [x] **Coleta durante a apuração** — o coletor pede só as seções que o TSE já marcou como
      transmitidas, e repete passadas até fechar (seção 5.1).
- [x] **Orquestração da noite** — `domingo.py`, com a conferência como trava e
      retomada por etapa. Para antes do deploy.
- [x] **Camada ao vivo** — `ao_vivo.py` mais `site/js/bu.js` e `site/js/ao_vivo.js`:
      2026 na tela a partir das 17h30, buscado no TSE pelo navegador (seção 9).
- [x] **Malha de 2026 antecipada** — trocada e publicada no sábado, para os links
      de domingo valerem a semana e para a madrugada não refazer o passo 31.
- [x] **Testes** — 117 em pytest (94 da coleta, 11 da camada ao vivo, 12 da malha), mais
      os dois que rodam JavaScript de verdade
      contra boletins reais (`teste_bu_js.py` e `teste_ao_vivo_js.py`).
- [x] **Especificação oficial do TSE lida** — leitura tolerante a mudança de formato,
      o quinto tipo de voto e a exigência de fase oficial (seção 3.5).
- [x] **Recusa em vez de exceção** — mudança de formato aparece como recusa contada,
      com limite de taxa e curto-circuito nos 200 primeiros boletins (seção 3.4).
- [ ] **Ensaio de sábado (03/10)**: rodar contra os parâmetros oficiais de 2026, para medir a
      taxa real e validar os caminhos antes da apuração. O que precisa ser conferido:
      descobrir a eleição pelo `ele-c.json`, baixar as 27 configurações de seção, contar as
      seções (esperado: ~499 mil), medir a vazão real de BU/s e baixar de novo o
      `consulta_cand_2026` com o cadastro já fechado.

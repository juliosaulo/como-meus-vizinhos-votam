# Limitações

Todo número deste projeto é uma estimativa territorial, não um registro. Esta página existe para
que ninguém precise descobrir isso sozinho depois.

## A limitação de fundo

**Ninguém sabe onde você vota a partir do seu endereço — nem este projeto.** O vínculo entre
eleitor e local de votação é dado pessoal e não é público, e é bom que não seja. O que se faz aqui
é inferir por proximidade: o local de votação mais próximo do seu endereço, dentro do seu
município.

Isso acerta na maioria dos casos porque a Justiça Eleitoral aloca seções por proximidade do
domicílio eleitoral. Mas erra para quem se mudou sem transferir o título, para quem foi realocado
por acessibilidade, e em zonas onde a alocação segue bairro em vez de distância pura.

E mesmo quando acerta o local, **o resultado mostrado é o daquele local de votação, não o do seu
quarteirão**. Um local atende milhares de eleitores espalhados por uma área grande, sobretudo no
interior.

## Cobertura, medida

Números da execução nacional. O arquivo `publicado/cobertura.json` traz a versão completa, com a
geocodificação por UF.

| Etapa | Cobertura | O que se perde |
|---|---|---|
| Locais de votação com coordenada | 84,1% | locais cujo nome/endereço no TSE não permitiu geocodificar |
| Votos dentro de alguma região | 87,4% | votos dados nos locais acima |
| Endereços atribuídos a uma região | 99,99% | endereços dos municípios sem nenhum local geocodificado |
| Acerto do índice de ruas | 96,40% (rua + bairro + número) | ver abaixo |

**A cobertura da geocodificação varia muito entre estados** — de 53,2% no Distrito Federal e 66,6%
no Pará a 93,3% em São Paulo, seguindo a qualidade do endereçamento em cada região. Por isso a
cobertura é publicada por UF, e não só como um número nacional.

**Os totais desta base não são totais eleitorais.** Ela cobre só o voto espacializável (87,4% no
país). Para número oficial, a fonte é o TSE.

## O acerto depende do que o usuário informa

Antes da tabela, o que "acerto" significa aqui. Ele compara **a consulta com a coordenada**: para
cada um dos 134.981 endereços sorteados do CNEFE existe uma resposta de referência — o local de
votação mais próximo daquela coordenada, dentro do município — e a resposta que a consulta devolve
quando se informa rua, e conforme o caso bairro e número. Acerto é as duas coincidirem; erro é a
consulta apontar outro local, mandando a pessoa para um lugar diferente do mais próximo de casa.

Não está sendo medido se a pessoa de fato vota ali: essa é a premissa da seção anterior, e não há
como verificá-la com dado público. Também ficam fora os locais sem coordenada, que aparecem na
tabela de cobertura.

Uma rua é uma linha, às vezes de quilômetros. Só o nome dela nem sempre basta para dizer onde a
pessoa vota, e o quanto falta depende do que mais ela souber informar:

| O que o usuário informa | Acerto | Só domicílios |
|---|---:|---:|
| Só a rua | 78,29% | 79,36% |
| Rua + bairro | 87,61% | 88,51% |
| Rua + número | 92,44% | 93,53% |
| Rua + bairro + número | **96,40%** | **97,10%** |

A segunda coluna é a que descreve o usuário do site: o índice desempata por domicílio, então acerta
mais onde mora gente do que em obras e comércios.

### Por que existe erro, se a regra é do próprio projeto?

A pergunta é justa: se "região" é definida como o local de votação mais próximo, e a atribuição é
feita endereço por endereço a partir da coordenada, ela é exata por construção. O erro não está na
atribuição — está na **compressão**.

O site não recebe uma coordenada, recebe texto: município, rua, e talvez bairro e número. O índice é
essa atribuição comprimida numa tabela indexada por texto, e aí endereços com coordenadas
diferentes, e portanto com respostas diferentes, passam a dividir a mesma chave. A chave só pode ter
uma resposta. **O erro medido é exatamente essa perda** — se fosse possível consultar pela
coordenada, o acerto seria 100%, e a medição não diria nada.

Os três mecanismos, com a distribuição medida numa amostra de 5.000 endereços de Rondônia (187
erros):

**Endereço sem número — 70% dos erros.** Todos os S/N de uma via dividem uma chave só. Na `RODOVIA
RO 383, S/N`, em Cacoal, a via atende 5 locais: a coordenada real de um endereço aponta uma escola,
e o índice devolve outra, 18,7 km adiante. As duas respostas estão certas para endereços diferentes
que compartilham a mesma chave.

**Número entre dois trechos — 14% dos erros.** A interpolação assume que a numeração avança junto
com o espaço, o que falha em linha rural. Na `LINHA UM` de Corumbiara, os trechos começam em 0, 1, 2
e 20; o número 4 cai no trecho que começa em 2, mas aquele endereço, pela coordenada, pertence ao
anterior.

**Número conhecido que ainda assim erra — 16% dos erros.** A chave existe no índice, mas o mesmo
"rua + número" existe em dois lugares do município: os dois lados de uma via em regiões diferentes,
uma numeração que recomeça, ou vias homônimas sob o mesmo nome. O índice guarda a região de maior
peso, e quem está do outro lado recebe a outra resposta.

Em resumo: a verdade é por endereço, o índice é por texto, e o erro é a fração de endereços que o
texto não distingue. É por isso que ele cai conforme o usuário informa mais — e por isso o bairro
ajuda mais no interior, onde o número não existe e a chave "rua" sozinha agrega uma via inteira de
dezenas de quilômetros.

**Endereço sem número** é o maior buraco: 23,8% dos endereços do país são S/N — sítios, linhas
rurais, vilas onde o CNEFE não registra numeração. Sem número, todos os endereços da via
compartilham a mesma resposta. Daí o bairro valer mais que o número exatamente onde o número falta:
em Goiás, rua + bairro acerta 89,5% contra 84,0% de rua + número; no Maranhão, 88,9% contra 83,1%.
Em São Paulo o número ganha com folga: 97,5% contra 87,2%.

Pelo caminho completo, o acerto vai de 93,5% em Goiás a 98,7% em São Paulo.

A consulta devolve, junto com a região, um **nível de confiança** e a lista de locais possíveis:

| Confiança | Significado |
|---|---|
| `exata` | só uma região é possível: a rua tem uma região só, o bairro informado tem uma só, ou o número digitado é o início de um trecho do índice |
| `interpolada` | o número cai entre dois inícios de trecho: a resposta é a do trecho anterior |
| `provavel` | várias regiões possíveis: a resposta é a de maior peso, e vem acompanhada das outras |

## O bairro, e o que ele não é

O desempate por bairro usa `DSC_LOCALIDADE`, do CNEFE, preenchida em 100% dos endereços. Mas ela
**não é exatamente o bairro**: é a *localidade* do IBGE, o nome que o recenseador registrou. Na
cidade do Rio de Janeiro são 597 valores distintos, contra 163 bairros oficiais — além dos bairros
aparecem comunidades, loteamentos, um "RIO DE JANEIRO" genérico com 47 mil endereços e alguns
valores sem sentido ("FACE 99999", "RUA E"). No interior são localidades rurais: "LINHA 617",
"ABUNA".

Isso não prejudica a precisão — uma divisão mais fina continua correta —, mas prejudica o
reconhecimento: a opção oferecida pode não ser o nome que a pessoa usaria. Por isso o site deve
chamar o campo de "bairro ou localidade", oferecer só as opções da rua escolhida, ordenadas por
tamanho, e manter sempre a saída "não sei".

**Vias sem nome** são o caso extremo que o bairro resolve. O CNEFE registra ruas sem denominação
com esse rótulo literal, e a chave do índice é município + nome da rua: em São Paulo, 715 regiões
diferentes atendem endereços de "VIELA SEM DENOMINACAO"; em Porto Velho, 27 atendem "RUA SEM
DENOMINACAO". Sem o bairro, a consulta é inutilizável nesses endereços; com ele, quase sempre vira
resposta única.

## Aproximações conhecidas

**Coordenada compartilhada nem sempre é o mesmo prédio.** Regiões são definidas por coordenada
exata, e locais de votação que dividem o mesmo ponto viram uma região só. São 3.120 pontos com mais
de um local (4,2% das regiões), e em 74,6% deles os endereços cadastrados são *diferentes* —
tipicamente localidades rurais distintas que o CNEFE não distingue. A região guarda um local
representativo e lista os demais, em vez de fingir que é um lugar só.

**O histórico é realocado para a malha de hoje.** Votos de 2018 aparecem na região de referência
mais próxima. Quando um local fechou e outro abriu perto, isso é razoável; quando a mudança foi
maior, o número de 2018 descreve um território levemente diferente do atual.

**A geocodificação tem erro residual.** A validação por amostra feita na construção da
geocodificação indicou ~98,7% de acerto nos pareamentos automáticos. O ~1,3% restante se comporta
como ruído: alguns endereços ficam ligados ao local de votação errado.

**Voto no exterior está fora.** Locais em Boston, Tóquio ou Bruxelas não têm correspondência no
CNEFE, que é um cadastro do território nacional.

**Sete municípios não têm nenhum local de votação geocodificado** e por isso não têm região
nenhuma — cerca de 12 mil endereços, em Goiás, Mato Grosso, Pará, Paraíba, Pernambuco e Tocantins.

## O que este projeto não é

Não é uma ferramenta para saber onde você vota — para isso existe o
[título de eleitor digital](https://www.tse.jus.br/) e o site do TSE, que dão a resposta oficial.

Não é uma fonte de resultado eleitoral. É uma leitura territorial de resultados que já são
públicos, com o recorte e as perdas descritos aqui.

Não identifica ninguém. O dado mais fino disponível é a urna, que agrega centenas de eleitores, e o
voto é secreto.

# Metodologia

## A pergunta e a inferência

O site responde "como votou a minha região". A palavra que carrega o peso é **região**, porque ela
não existe no dado de origem — precisa ser construída.

O que existe é: o resultado de cada urna, e o prédio onde a urna ficou. O que não existe, e
deliberadamente não deveria existir, é o vínculo entre eleitor e endereço. Então "a região de um
local de votação" é definida aqui como **a área cujos endereços têm aquele local como o mais
próximo, dentro do mesmo município**.

A premissa é que quem mora perto de um local de votação vota nele. A Justiça Eleitoral de fato
aloca seções por proximidade do domicílio eleitoral, então a premissa tem base — mas ela é
estatística, não individual. Quem se mudou e não transferiu o título, quem vota onde trabalha, quem
foi realocado por acessibilidade: todos contrariam a regra.

Por isso o site marca no mapa **o local de votação**, com nome e endereço, em vez de pintar uma
área. A informação que o dado sustenta é "este é o local de votação que atende o seu endereço"; a
área é uma consequência, não uma medição.

## A atribuição: local mais próximo no mesmo município

Cada endereço do CNEFE é ligado ao local de votação mais próximo, consultando uma árvore de vizinho
mais próximo (`scipy.cKDTree`) montada por município. Duas condições fazem essa resposta ser a
certa:

- **Restrição ao município.** A busca só considera locais do mesmo município do endereço. O eleitor
  vota no município do seu domicílio eleitoral; sem a restrição, um endereço perto da divisa seria
  atribuído a uma cidade onde a pessoa não vota.
- **Projeção métrica.** Toda distância é calculada em SIRGAS 2000 / Brasil Polyconic (EPSG:5880),
  em metros. Em graus, um grau de longitude vale ~111 km no norte do país e ~78 km no sul: o
  vizinho mais próximo medido em graus é outra resposta, enviesada por latitude.

## O índice de ruas

Atribuir 111 milhões de endereços a regiões produz uma tabela grande demais para servir a um site.
A compressão explora o fato de que numeração de rua é ordenada no espaço: percorrendo uma rua do
número 1 em diante, a região muda poucas vezes. Guarda-se então só **onde muda**:

```
Rua Dr. Brandão → [[1, 87], [520, 88], [1200, 87]]
```

Do 1 ao 519, região 87; do 520 ao 1199, região 88; do 1200 em diante, de novo a 87. Consultar é uma
busca binária pelo maior início menor ou igual ao número digitado. No país inteiro isso leva 54,8
milhões de combinações de rua × bairro × número × região a 6,5 milhões de trechos — 8 vezes menor.

A região "volta" a ser a mesma porque ruas longas cruzam a fronteira entre dois locais de votação
mais de uma vez, e os dois lados de uma via podem cair em regiões distintas. O índice registra o
que a geografia diz, sem suavizar.

Quando o mesmo número aparece em duas regiões (lados opostos, condomínio na divisa), fica a região
com **mais domicílios** naquele número. O desempate precisa ser por peso real: decidir por ordem de
chegada equivaleria a sortear, e custaria quase 5 pontos percentuais de acerto ponta a ponta —
diferença medida no piloto de Rondônia.

O peso é domicílio, e não endereço, porque a pergunta é onde mora gente: um endereço do CNEFE pode
ser um comércio, uma escola ou uma obra. Todos continuam no índice, para que qualquer rua seja
pesquisável; só não pesam no desempate. Numa rua sem domicílio nenhum, o peso cai para a contagem
de endereços.

## O bairro, a segunda chave

O número não existe para 23,8% dos endereços, e o site pode preferir não pedi-lo. Sem ele, a
resposta de uma rua ambígua é a região de maior peso — 78,3% de acerto, medido. O bairro
(`DSC_LOCALIDADE`, do CNEFE) entra então como segunda chave: restringe as regiões candidatas às
daquela localidade, e leva o acerto a 87,6% sem número nenhum, ou a 96,4% quando o número também é
informado.

Ele é publicado só onde desempata: nas ruas ambíguas que passam por mais de um bairro. É também o
único jeito de separar as vias que o CNEFE registra sem nome — em São Paulo, "VIELA SEM
DENOMINACAO" reúne 715 regiões diferentes sob um rótulo só.

## A malha de regiões e o tempo

Locais de votação abrem e fecham entre eleições: 96,0% das regiões com voto em 2022 também tiveram
voto em 2018.

A malha é **congelada no ano de referência** — o ano mais recente com resultado, que é onde a
pessoa vota hoje. Os votos de anos anteriores são realocados para a região de referência mais
próxima dentro do mesmo município; hoje, 410 regiões que só existiram em 2018 têm seus votos
realocados assim. Sem isso, um voto de 2018 sumiria da tela.

É uma aproximação com efeito real: quando um local fecha e outro abre a dois quarteirões, tratá-los
como a mesma área é razoável; quando a mudança é maior, o número de 2018 daquela região descreve um
território levemente diferente.

Quando sair o resultado de 2026, os locais daquele ano passam a ser a referência e o histórico é
realocado de novo. A atribuição de endereços (passo 31) é refeita, porque a lista de locais muda.

## Fontes

| Dado | Origem | Uso |
|---|---|---|
| Votação por seção | TSE, dados abertos — arquivo nacional (Presidente) e por UF (demais cargos) | resultado por urna |
| Cadastro de candidatos | TSE, `consulta_cand` | o partido de cada candidato, que não vem na votação |
| CNEFE | IBGE, Censo 2022 | 111M endereços com rua, número e coordenada |
| Locais geocodificados | artefato importado — ver [PROVENIENCIA.md](dados_importados/PROVENIENCIA.md) | nome, endereço e coordenada de cada local de votação |

Presidente vem num arquivo separado dos demais cargos: no TSE, "Eleição Geral Federal" e "Eleições
Gerais Estaduais" são registros de eleição distintos, com downloads distintos, mesmo ocorrendo no
mesmo dia e na mesma urna.

## Convenções de contagem

- **Voto válido** = nominal + legenda, a convenção legal brasileira. É o denominador do percentual
  de cada candidato. Branco e nulo ficam fora dele e são reportados à parte, como proporção do
  comparecimento.
- **Voto de legenda** é identificado pelo sequencial de candidato `-3`, ou `-1` (vazio) com número
  de partido de 2 dígitos — a forma em que ele vem no arquivo de Deputado Federal do DF em 2018.
- **Presidente não tem voto de legenda** (é eleição majoritária), então ali válido = nominal.
- **Turnos nunca são somados.** Presidente teve 2º turno em 2018 e 2022; somar os dois conta o
  mesmo eleitor duas vezes.
- **Voto soma, atributo de lugar deduplica.** Vários locais de votação podem dividir o mesmo
  prédio: o voto de cada um é distinto e se soma, o endereço é o mesmo e se deduplica.

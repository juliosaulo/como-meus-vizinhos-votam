/* O resultado de 2026 ao vivo, buscado no TSE pelo navegador de quem consulta.
 *
 * Enquanto a apuração corre, o resultado por local de votação não existe em
 * nenhum arquivo agregado: os do TSE param na zona eleitoral. O único lugar onde
 * ele existe é o boletim de cada urna — e o TSE serve esses arquivos com CORS
 * liberado, com cache curto, e com limite de requisições **por IP**. Então cada
 * visitante busca os boletins do seu próprio local, com o seu próprio orçamento,
 * e nada disso passa pelo nosso servidor.
 *
 * O desenho inteiro obedece a uma regra: **se qualquer coisa falhar, a página é
 * a página de ontem.** Sem bloco de 2026, sem erro na tela. Uma camada que
 * mostra número errado numa noite de eleição é pior que uma camada que não
 * aparece.
 *
 * Quatro cuidados que não são óbvios:
 *
 * - **Nunca pedir arquivo que não existe.** Seção que ainda não transmitiu dá
 *   404, e o TSE avisa que 404 também pode gerar bloqueio — que aqui cairia no
 *   IP do visitante, deixando-o sem acessar nem o site do TSE. Por isso o módulo
 *   lê antes a configuração de seções, que diz quais já geraram arquivo.
 * - **O boletim manda sobre o nosso mapa.** O mapa de seções diz o que pedir; o
 *   local de verdade é o que o boletim declara. Boletim que diz pertencer a
 *   outro local é descartado desta região.
 * - **Conferir antes de exibir.** Boletim cujo voto não soma o comparecimento é
 *   descartado, e nenhum candidato pode ter mais voto nesta região do que tem no
 *   município inteiro, segundo o arquivo oficial.
 * - **Só completa o que falta.** Se o arquivo publicado já tiver 2026 — depois
 *   da totalização definitiva —, esta camada não é chamada.
 */

import * as bu from "./bu.js";

const BASE_PUBLICADO = new URL("../../publicado/ao_vivo/", import.meta.url).href;

/* Quantas requisições ao TSE em paralelo. O teto dele é 100 por segundo por IP;
 * uma consulta usa de 9 a 25 requisições, então isto é folga, não limite. */
const EM_PARALELO = 12;

/* A configuração de seções diz quais urnas já transmitiram, e muda o tempo todo.
 * Três minutos é o bastante para não rebuscar a cada clique e pouco o bastante
 * para o contador de urnas não envelhecer na tela. */
const VALIDADE_CONFIG_MS = 3 * 60 * 1000;
const VALIDADE_AGREGADO_MS = 60 * 1000;

const cache = new Map();

/** A camada só acorda no dia da eleição. Antes disso não há urna nenhuma para
 *  pedir, e avisar que "nenhuma urna enviou boletim" na véspera confunde quem
 *  está só consultando o histórico. Sem data no config, fica sempre ligada —
 *  é o que o ensaio usa. */
function jaEhODia(data) {
  if (!data) return true;
  const [dia, mes, ano] = String(data).split("/").map(Number);
  if (!dia || !mes || !ano) return true;
  const hoje = new Date();
  const inicio = new Date(ano, mes - 1, dia);
  return hoje >= inicio;
}

/** Busca com cache por tempo. Erro não é guardado: a próxima tentativa tenta. */
async function comCache(chave, validadeMs, buscar) {
  const agora = Date.now();
  const guardado = cache.get(chave);
  if (guardado && agora - guardado.quando < validadeMs) return guardado.valor;
  const valor = await buscar();
  cache.set(chave, { quando: agora, valor });
  return valor;
}

const seis = n => String(Number(n)).padStart(6, "0");
const quatro = n => String(Number(n)).padStart(4, "0");

async function json(url) {
  const resposta = await fetch(url);
  if (!resposta.ok) return null;
  return resposta.json();
}

/* ------------------------------------------------------------------ TSE */

const caminhoUrna = (cfg, uf) =>
  `${cfg.base}/${cfg.ambiente}/${cfg.ciclo}/arquivo-urna/${cfg.pleito}`;

function caminhoAux(cfg, uf, municipio, zona, secao) {
  const z = quatro(zona);
  const s = quatro(secao);
  return `${caminhoUrna(cfg, uf)}/dados/${uf}/${municipio}/${z}/${s}`
    + `/p${seis(cfg.pleito)}-${uf}-m${municipio}-z${z}-s${s}-aux.json`;
}

/** O resultado oficial agregado. `abrangencia` é "br", a UF, ou UF+município. */
function caminhoAgregado(cfg, uf, abrangencia, cargo) {
  const pasta = abrangencia === "br" ? "br" : uf;
  return `${cfg.base}/${cfg.ambiente}/${cfg.ciclo}/${cfg.eleicao}/dados/${pasta}`
    + `/${abrangencia}-c${quatro(cargo)}-e${seis(cfg.eleicao)}-u.json`;
}

/** Qual transmissão vale: a totalizada; antes disso, a última recebida.
 *  Mesma regra do coletor — o último hash pode ser o rejeitado de uma
 *  retransmissão. */
function escolherHash(aux) {
  const hashes = aux?.hashes ?? [];
  const totalizados = hashes.filter(h => (h.st ?? "").toLowerCase().startsWith("totaliz"));
  const candidatos = totalizados.length ? totalizados : hashes;
  return candidatos[candidatos.length - 1] ?? null;
}

/** As seções da UF que já geraram arquivo auxiliar, como "zona/secao".
 *  O campo `da` só existe depois dessa geração — é o próprio TSE dizendo o que
 *  já pode ser pedido. */
async function transmitidas(cfg, uf, municipioTse) {
  const chave = `cs:${uf}`;
  const porMunicipio = await comCache(chave, VALIDADE_CONFIG_MS, async () => {
    const url = `${caminhoUrna(cfg, uf)}/config/${uf}/${uf}-p${seis(cfg.pleito)}-cs.json`;
    const config = await json(url);
    const mapa = new Map();
    for (const abrangencia of config?.abr ?? []) {
      for (const municipio of abrangencia.mu ?? []) {
        const conjunto = new Set();
        for (const zona of municipio.zon ?? []) {
          for (const secao of zona.sec ?? []) {
            if (secao.da) conjunto.add(`${Number(zona.cd)}/${Number(secao.ns)}`);
          }
        }
        mapa.set(municipio.cd, conjunto);
      }
    }
    return mapa;
  });
  return porMunicipio?.get(municipioTse) ?? null;
}

/* ------------------------------------------------------------------ soma */

/** Junta os boletins de uma região num resultado, na forma do JSON publicado.
 *
 *  Separado da parte de rede de propósito: é aqui que mora a aritmética, e é
 *  isto que o teste exercita sem tocar na internet. */
export function somar(boletins, { cfg, locaisDaRegiao, candidatos, uf }) {
  const porCargo = new Map();
  let usados = 0;

  for (const boletim of boletins) {
    if (!boletim) continue;
    if (boletim.fase !== "oficial") continue;                   // simulado/treino: fora
    if (bu.conferir(boletim).length) continue;                  // não fecha: fora
    if (!locaisDaRegiao.has(boletim.local)) continue;           // outro local: fora
    const cargos = boletim.eleicoes[cfg.eleicao];
    if (!cargos) continue;
    usados++;
    for (const cargo of cargos) {
      const nome = cfg.cargos[String(cargo.codigo)];
      if (!nome) continue;
      const acumulado = porCargo.get(nome)
        ?? { comparecimento: 0, votaveis: new Map(), branco: 0, nulo: 0 };
      acumulado.comparecimento += cargo.comparecimento;
      for (const voto of cargo.votos) {
        if (voto.tipo === "branco") acumulado.branco += voto.quantidade;
        else if (voto.tipo === "nulo") acumulado.nulo += voto.quantidade;
        else {
          const chave = `${voto.tipo}:${voto.numero}`;
          acumulado.votaveis.set(chave,
            (acumulado.votaveis.get(chave) ?? 0) + voto.quantidade);
        }
      }
      porCargo.set(nome, acumulado);
    }
  }

  if (!usados) return null;

  const resultados = {};
  const naoNominal = {};
  const totais = {};
  for (const [nome, dados] of porCargo) {
    const lista = [];
    let validos = 0;
    for (const [chave, votos] of dados.votaveis) {
      const [tipo, numero] = chave.split(":");
      validos += votos;
      const quem = nomeDoVotavel(tipo, numero, nome, candidatos, uf);
      lista.push({ nome: quem.nome, partido: quem.partido, votos });
    }
    for (const item of lista) item.pct = validos ? arredondar(item.votos * 100 / validos) : 0;
    lista.sort((a, b) => b.votos - a.votos);
    resultados[nome] = lista;

    const comparecimento = dados.comparecimento;
    naoNominal[nome] = {
      branco: { votos: dados.branco, pct: proporcao(dados.branco, comparecimento) },
      nulo: { votos: dados.nulo, pct: proporcao(dados.nulo, comparecimento) },
    };
    totais[nome] = comparecimento;
  }
  return { resultados, naoNominal, totais, urnas: usados };
}

const arredondar = n => Math.round(n * 100) / 100;
const proporcao = (parte, todo) => (todo ? arredondar(parte * 100 / todo) : 0);

/** O número do boletim virando nome. Legenda é o partido; número sem cadastro
 *  aparece como o próprio número, nunca como vazio. */
function nomeDoVotavel(tipo, numero, cargo, candidatos, uf) {
  const tabela = cargo === "presidente" ? candidatos.presidente : candidatos[uf.toUpperCase()];
  const achado = tabela?.[numero];
  if (tipo === "legenda") {
    return { nome: achado?.partido ?? `Partido ${numero}`, partido: achado?.partido ?? null };
  }
  if (achado) return { nome: achado.nome, partido: achado.partido };
  return { nome: `Número ${numero}`, partido: null };
}

/* ------------------------------------------------------------------ guarda */

/** Nenhum candidato pode ter nesta região mais voto do que tem no município.
 *
 *  É a conferência cruzada mais forte que cabe numa requisição: o arquivo
 *  oficial do município é pequeno, exato, e a região é um subconjunto dele. Um
 *  parser inflando número é pego aqui. */
function dentroDoMunicipio(resultados, agregados) {
  for (const [cargo, lista] of Object.entries(resultados)) {
    const oficial = agregados[cargo];
    if (!oficial) continue;
    for (const item of lista) {
      const limite = oficial.get(item.nome);
      if (limite !== undefined && item.votos > limite) return false;
    }
  }
  return true;
}

function votosOficiaisPorNome(conteudo) {
  const mapa = new Map();
  for (const cargo of conteudo?.carg ?? []) {
    for (const agremiacao of cargo.agr ?? []) {
      for (const partido of agremiacao.par ?? []) {
        for (const candidato of partido.cand ?? []) {
          mapa.set(candidato.nmu, Number(candidato.vap));
        }
      }
    }
  }
  return mapa;
}

/** Número → nome e partido, uma vez por sessão. É a mesma tabela para o
 *  resultado do local e para os agregados oficiais — ver `agregadoDoOficial`. */
function tabelaDeCandidatos(base, uf) {
  return comCache(`cand:${base}:${uf}`, Infinity, async () => ({
    presidente: await json(`${base}candidatos/presidente.json`) ?? {},
    [uf.toUpperCase()]: await json(`${base}candidatos/${uf.toUpperCase()}.json`) ?? {},
  }));
}

/* ------------------------------------------------------------------ busca */

async function emLotes(itens, tamanho, tarefa) {
  const saida = [];
  for (let i = 0; i < itens.length; i += tamanho) {
    saida.push(...await Promise.all(itens.slice(i, i + tamanho).map(tarefa)));
  }
  return saida;
}

/** A configuração da camada, ou null se ela estiver desligada.
 *  Apagar `publicado/ao_vivo/config.json` do servidor desliga tudo.
 *
 *  `base` existe para o teste poder apontar para uma eleição real e encerrada —
 *  é o que permite ver a camada funcionando com dado de verdade antes do dia. */
export function configurar(base = BASE_PUBLICADO) {
  return comCache(`cfg:${base}`, VALIDADE_CONFIG_MS, async () => {
    try {
      const cfg = await (await fetch(`${base}config.json`,
        { cache: "no-store" })).json();
      return cfg?.ativo && jaEhODia(cfg.data) ? cfg : null;
    } catch {
      return null;
    }
  });
}

/** O resultado ao vivo de uma região, ou null se não houver o que mostrar.
 *
 *  Nunca levanta: qualquer falha devolve null, e quem chama segue com o
 *  histórico. */
export async function resultadoDaRegiao({ idRegiao, municipioIbge, uf,
  base = BASE_PUBLICADO }) {
  try {
    const cfg = await configurar(base);
    if (!cfg) return null;

    const mapa = await comCache(`sec:${base}:${municipioIbge}`, Infinity,
      () => json(`${base}secoes/${municipioIbge}.json`));
    const secoes = mapa?.regioes?.[String(idRegiao)];
    if (!secoes?.length) return null;

    const municipioTse = mapa.municipio_tse;
    const ufMinuscula = (uf ?? mapa.uf).toLowerCase();
    const disponiveis = await transmitidas(cfg, ufMinuscula, municipioTse);
    if (!disponiveis) return null;

    const pedir = secoes.filter(([zona, , secao]) => disponiveis.has(`${zona}/${secao}`));
    if (!pedir.length) return null;

    const candidatos = await tabelaDeCandidatos(base, ufMinuscula);

    const boletins = await emLotes(pedir, EM_PARALELO, async ([zona, , secao]) => {
      try {
        const aux = await json(caminhoAux(cfg, ufMinuscula, municipioTse, zona, secao));
        const hash = escolherHash(aux);
        const arquivo = hash?.arq?.find(a => a.tp === "bu");
        if (!hash || !arquivo) return null;
        const url = `${caminhoAux(cfg, ufMinuscula, municipioTse, zona, secao)
          .replace(/\/[^/]+$/, "")}/${hash.hash}/${arquivo.nm}`;
        const resposta = await fetch(url);
        if (!resposta.ok) return null;
        return bu.ler(await resposta.arrayBuffer());
      } catch {
        return null;
      }
    });

    const locaisDaRegiao = new Set(secoes.map(([, local]) => local));
    const somado = somar(boletins, { cfg, locaisDaRegiao, candidatos, uf: ufMinuscula });
    if (!somado) return null;

    const agregados = {};
    for (const [codigo, nome] of Object.entries(cfg.cargos)) {
      const conteudo = await comCache(`agr:${municipioTse}:${codigo}`, VALIDADE_AGREGADO_MS,
        () => json(caminhoAgregado(cfg, ufMinuscula, ufMinuscula + municipioTse, codigo)));
      if (conteudo) agregados[nome] = votosOficiaisPorNome(conteudo);
    }
    if (!dentroDoMunicipio(somado.resultados, agregados)) return null;

    return {
      ano: String(cfg.ano),
      turno: String(cfg.turno),
      ...somado,
      urnasPedidas: secoes.length,
      atualizado: new Date(),
    };
  } catch {
    return null;
  }
}


/* --------------------------------------------------- agregados oficiais */

const caixaDeTitulo = texto => String(texto ?? "").toLowerCase()
  .replace(/(^|[\s'-])([a-zà-ú])/g, (_, antes, letra) => antes + letra.toUpperCase());

/** Converte o arquivo oficial do TSE na forma dos agregados publicados.
 *
 *  O nome sai da **nossa** tabela de candidatos, pelo número, e não do nome que
 *  o arquivo oficial traz. O motivo é a aba comparativo: ela casa o local com o
 *  município pelo nome do candidato, e os dois lados precisam carregar a mesma
 *  string. Deixar cada lado formatar o seu nome faria "Flávio Bolsonaro" e
 *  "FLAVIO BOLSONARO" virarem dois candidatos diferentes, e o gráfico jogaria
 *  tudo em "Outros". O nome oficial fica como reserva. */
function agregadoDoOficial(conteudo, tabela) {
  const lista = [];
  for (const cargo of conteudo?.carg ?? []) {
    for (const agremiacao of cargo.agr ?? []) {
      for (const partido of agremiacao.par ?? []) {
        const legenda = Number(partido.tvtl ?? 0);
        if (legenda) {
          lista.push({ nome: partido.sg, partido: partido.sg, votos: legenda });
        }
        for (const candidato of partido.cand ?? []) {
          const nosso = tabela?.[String(candidato.n)];
          lista.push({ nome: nosso?.nome ?? caixaDeTitulo(candidato.nmu),
            partido: nosso?.partido ?? partido.sg, votos: Number(candidato.vap) });
        }
      }
    }
  }
  const validos = lista.reduce((soma, c) => soma + c.votos, 0);
  for (const item of lista) {
    item.pct = validos ? Math.round(item.votos * 10000 / validos) / 100 : 0;
  }
  lista.sort((a, b) => b.votos - a.votos);
  const secoes = conteudo?.s ?? {};
  return {
    lista,
    apurado: {
      secoes: Number(secoes.ts ?? 0),
      totalizadas: Number(secoes.st ?? 0),
      pct: Number(String(secoes.pst ?? "0").replace(",", ".")),
    },
  };
}

/** Município, estado e Brasil do ano corrente, direto do TSE.
 *
 *  Estes três o TSE publica prontos e exatos — uma requisição cada, com o
 *  percentual oficial de seções totalizadas. Não há por que somar os nossos
 *  boletins para chegar a um número que já existe, e melhor: este é o número
 *  que a imprensa vai estar citando na mesma hora. */
export async function agregadosAoVivo({ municipioIbge, uf, base = BASE_PUBLICADO }) {
  try {
    const cfg = await configurar(base);
    if (!cfg) return null;
    const mapa = await comCache(`sec:${base}:${municipioIbge}`, Infinity,
      () => json(`${base}secoes/${municipioIbge}.json`));
    if (!mapa) return null;
    const ufMinuscula = (uf ?? mapa.uf).toLowerCase();

    const candidatos = await tabelaDeCandidatos(base, ufMinuscula);
    const niveis = {
      municipio: ufMinuscula + mapa.municipio_tse,
      uf: ufMinuscula,
      brasil: "br",
    };
    const saida = {};
    for (const [nivel, abrangencia] of Object.entries(niveis)) {
      const porCargo = {};
      let apurado = null;
      for (const [codigo, nomeCargo] of Object.entries(cfg.cargos)) {
        const conteudo = await comCache(`agr:${abrangencia}:${codigo}`, VALIDADE_AGREGADO_MS,
          () => json(caminhoAgregado(cfg, ufMinuscula, abrangencia, codigo)));
        if (!conteudo) continue;
        const tabela = nomeCargo === "presidente"
          ? candidatos.presidente : candidatos[ufMinuscula.toUpperCase()];
        const { lista, apurado: situacao } = agregadoDoOficial(conteudo, tabela);
        if (!lista.length) continue;
        porCargo[nomeCargo] = { [cfg.ano]: { [cfg.turno]: lista } };
        apurado = apurado ?? situacao;
      }
      if (Object.keys(porCargo).length) saida[nivel] = { ...porCargo, apurado };
    }
    return Object.keys(saida).length ? { ano: cfg.ano, turno: cfg.turno, ...saida } : null;
  } catch {
    return null;
  }
}

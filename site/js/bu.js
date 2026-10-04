/* Leitor do Boletim de Urna, no navegador.
 *
 * Porte de `coleta_2026/der.py` e `coleta_2026/bu.py`. Existe porque, durante a
 * apuração, o site busca o resultado de 2026 direto no servidor do TSE — e o
 * boletim é ASN.1 em DER, binário, sem schema público. É o único arquivo com
 * resultado por local de votação; os agregados do TSE param na zona eleitoral.
 *
 * Duas regras guiaram o porte:
 *
 * 1. **Mesma estrutura de saída do Python**, campo por campo, para que o teste
 *    possa comparar as duas implementações boletim a boletim (ver
 *    `site/dev/teste_bu.html`). Se as duas divergirem, é bug numa delas.
 * 2. **Recusar em vez de inventar.** Em Python, ler um campo que não existe
 *    levanta IndexError; em JavaScript devolve `undefined` e o erro só aparece
 *    três linhas depois, como um número errado. Então cada acesso é conferido, e
 *    qualquer surpresa vira `BoletimInvalido` — que a camada ao vivo trata como
 *    "sem resultado", nunca como resultado.
 */

export class BoletimInvalido extends Error {}

const CLASSE_CONTEXTO = 2;

/* Tipos de voto, do enumerado `TipoVoto` da especificação ASN.1 do TSE. O quinto
 * é o cargo sem candidato nenhum: sem reconhecê-lo, `conferir` acusaria tipo
 * desconhecido e a urna inteira seria descartada. */
const TIPOS_DE_VOTO = {
  1: "nominal", 2: "branco", 3: "nulo", 4: "legenda", 5: "cargo_sem_candidato",
};

/* Fase em que o arquivo foi gerado. A urna gera boletim em simulado e em
 * treinamento com a mesma estrutura do oficial — mostrar um deles na tela seria
 * publicar voto que não existiu. */
const FASES = { 1: "simulado", 2: "oficial", 3: "treinamento" };

const exigir = (condicao, mensagem) => {
  if (!condicao) throw new BoletimInvalido(mensagem);
};

/* ------------------------------------------------------------------ DER */

/** Lê uma sequência de nós DER no intervalo dado: etiqueta, tamanho, conteúdo. */
function lerNos(dados, posicao = 0, fim = dados.length) {
  const nos = [];
  while (posicao < fim) {
    exigir(posicao + 2 <= fim, "DER truncado: falta etiqueta ou tamanho");
    const etiqueta = dados[posicao++];
    const classe = etiqueta >> 6;
    const construido = Boolean(etiqueta & 0x20);
    let numero = etiqueta & 0x1f;
    if (numero === 0x1f) {           // etiqueta longa
      numero = 0;
      for (;;) {
        exigir(posicao < fim, "DER truncado: etiqueta longa sem fim");
        const byte = dados[posicao++];
        numero = (numero << 7) | (byte & 0x7f);
        if (!(byte & 0x80)) break;
      }
    }

    exigir(posicao < fim, "DER truncado: falta o tamanho");
    const primeiro = dados[posicao++];
    let tamanho = primeiro;
    if (primeiro & 0x80) {
      const bytes = primeiro & 0x7f;
      exigir(bytes <= 4 && posicao + bytes <= fim, "DER com tamanho inválido");
      tamanho = 0;
      for (let i = 0; i < bytes; i++) tamanho = tamanho * 256 + dados[posicao++];
    }
    exigir(posicao + tamanho <= fim, "DER truncado: conteúdo menor que o anunciado");

    const conteudo = dados.subarray(posicao, posicao + tamanho);
    const no = { classe, construido, numero, conteudo, filhos: [] };
    if (construido) no.filhos = lerNos(dados, posicao, posicao + tamanho);
    nos.push(no);
    posicao += tamanho;
  }
  return nos;
}

/** O conteúdo como inteiro com sinal, que é como o DER guarda. */
function inteiro(no) {
  exigir(no && !no.construido, "esperava um inteiro, veio um nó com filhos");
  const { conteudo } = no;
  if (conteudo.length === 0) return 0;
  exigir(conteudo.length <= 6, `inteiro com ${conteudo.length} bytes`);
  let valor = 0;
  for (const byte of conteudo) valor = valor * 256 + byte;
  if (conteudo[0] & 0x80) valor -= Math.pow(256, conteudo.length);
  return valor;
}

/** Campos com etiqueta de contexto guardam o inteiro cru, sem tipo universal. */
function inteiroDeContexto(no) {
  exigir(no && !no.construido,
    `esperava um inteiro cru em [ctx${no?.numero}], veio um nó com filhos`);
  exigir(no.conteudo.length <= 8, `inteiro de contexto com ${no.conteudo.length} bytes`);
  let valor = 0;
  for (const byte of no.conteudo) valor = valor * 256 + byte;
  return valor;
}

/** Acessa um campo pela posição, dizendo qual campo faltou quando falta. */
function filho(no, posicao, nome) {
  exigir(no && no.construido, `${nome}: esperava um nó com filhos`);
  exigir(no.filhos.length > posicao,
    `${nome}: esperava ao menos ${posicao + 1} campo(s), veio ${no.filhos.length}`);
  return no.filhos[posicao];
}

/* ----------------------------------------------------------------- forma
 *
 * O formato do BU muda entre eleições: comparando a especificação ASN.1 que o
 * TSE publicou em 2022 com um boletim real de 2024, `ResultadoVotacaoPorEleicao`
 * passou de 3 para 7 campos, e o leitor oficial do TSE com a especificação de
 * 2022 não lê um arquivo de 2024. Ler por posição fixa seria apostar que 2026
 * não mexeu em nada.
 *
 * Então: tenta a posição conhecida e, se o campo não tiver a forma esperada,
 * procura entre os outros. O que nunca acontece é aceitar um campo que não
 * parece o que deveria ser. */

const ehVotavel = no =>
  no.construido && no.filhos.length >= 2
  && !no.filhos[0].construido && no.filhos[0].classe === CLASSE_CONTEXTO
  && !no.filhos[1].construido && no.filhos[1].classe === CLASSE_CONTEXTO;

const ehListaDeCargos = no =>
  no.construido && no.filhos.length > 0
  && no.filhos.every(c => c.construido && c.filhos.length >= 3
    && c.filhos[2].construido && c.filhos[2].filhos.length > 0
    && c.filhos[2].filhos.every(ehVotavel));

const ehListaDeGrupos = no =>
  no.construido && no.filhos.length > 0
  && no.filhos.every(g => g.construido && g.filhos.length >= 3
    && ehListaDeCargos(g.filhos[2]));

const ehListaDeEleicoes = no =>
  no.construido && no.filhos.length > 0
  && no.filhos.every(e => e.construido && e.filhos.some(ehListaDeGrupos));

/** O campo na posição conhecida; se não tiver a forma certa, o que tiver. */
function campoPorForma(no, posicao, forma, nome) {
  exigir(no?.construido, `${nome}: esperava um nó com filhos`);
  if (no.filhos.length > posicao && forma(no.filhos[posicao])) return no.filhos[posicao];
  const achado = no.filhos.find(forma);
  exigir(achado, `${nome}: nenhum dos ${no.filhos.length} campos tem a forma esperada`);
  return achado;
}

/* ------------------------------------------------------------------ boletim */

/** Lê os bytes de um arquivo `-bu.dat`. Levanta `BoletimInvalido` em qualquer
 *  surpresa de estrutura — nunca devolve boletim montado de campo que não confere. */
export function ler(bytes) {
  const dados = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  try {
    return montar(dados);
  } catch (erro) {
    if (erro instanceof BoletimInvalido) throw erro;
    throw new BoletimInvalido(`estrutura inesperada: ${erro.message}`);
  }
}

function montar(dados) {
  const raiz = lerNos(dados);
  exigir(raiz.length > 0, "arquivo vazio");
  const envelope = raiz[0];
  exigir(envelope.construido && envelope.filhos.length > 0,
    "não parece um BU: envelope sem campos");

  const internoBruto = envelope.filhos[envelope.filhos.length - 1];
  exigir(!internoBruto.construido && internoBruto.conteudo.length > 0,
    "não parece um BU: o último campo do envelope não é o boletim");
  const internoNos = lerNos(internoBruto.conteudo);
  exigir(internoNos.length > 0, "não parece um BU: boletim vazio");
  const interno = internoNos[0];

  const pleito = inteiroDeContexto(filho(filho(interno, 0, "cabeçalho"), 1, "pleito"));
  const noFase = filho(interno, 1, "fase");
  const fase = FASES[noFase.construido ? -1 : inteiro(noFase)] ?? "desconhecida";

  const identificacao = filho(interno, 3, "identificação da seção");
  const municipioZona = filho(identificacao, 0, "município e zona");
  const municipio = inteiro(filho(municipioZona, 0, "município"));
  const zona = inteiro(filho(municipioZona, 1, "zona"));
  const local = inteiro(filho(identificacao, 1, "local"));
  const secao = inteiro(filho(identificacao, 2, "seção"));
  for (const [nome, valor] of [["município", municipio], ["zona", zona],
    ["local", local], ["seção", secao]]) {
    exigir(valor >= 0, `${nome} negativo: ${valor}`);
  }
  exigir(municipio <= 99999, `código de município fora da faixa: ${municipio}`);

  const resultados = campoPorForma(interno, 8, ehListaDeEleicoes,
    "resultados por eleição");

  const eleicoes = {};
  for (const eleicao of resultados.filhos) {
    const codigo = inteiro(filho(eleicao, 0, "código da eleição"));
    const grupos = campoPorForma(eleicao, 4, ehListaDeGrupos,
      `grupos da eleição ${codigo}`);
    const cargos = [];
    for (const grupo of grupos.filhos) {
      const comparecimento = inteiro(filho(grupo, 1, "comparecimento"));
      exigir(comparecimento >= 0, `comparecimento negativo: ${comparecimento}`);
      for (const totais of filho(grupo, 2, "totais por cargo").filhos) {
        const votaveis = filho(totais, 2, "votáveis");
        exigir(votaveis.filhos.length > 0, "cargo sem nenhum votável");
        cargos.push({
          codigo: inteiroDeContexto(filho(totais, 0, "código do cargo")),
          comparecimento,
          votos: votaveis.filhos.map(lerVoto),
        });
      }
    }
    exigir(cargos.length > 0, `eleição ${codigo} sem nenhum cargo`);
    eleicoes[codigo] = cargos;
  }

  return {
    municipio: String(municipio).padStart(5, "0"),
    zona, local, secao, pleito, fase, eleicoes,
  };
}

function lerVoto(no) {
  const codigo = inteiroDeContexto(filho(no, 0, "tipo do voto"));
  const quantidade = inteiro(filho(no, 1, "quantidade de votos"));
  exigir(quantidade >= 0, `quantidade de votos negativa: ${quantidade}`);
  const identificacao = filho(no, 2, "identificação do votável");
  let partido = null;
  let numero = null;
  // Só voto em alguém tem identificação com partido e número; branco e nulo
  // trazem outra coisa nessa posição (o ordinal do votável).
  if (identificacao.construido && identificacao.filhos.length === 2) {
    partido = inteiro(identificacao.filhos[0]);
    numero = inteiro(identificacao.filhos[1]);
  }
  return {
    tipo: TIPOS_DE_VOTO[codigo] ?? `desconhecido_${codigo}`,
    quantidade, partido, numero,
  };
}

/** Problemas que um BU bem formado não pode ter.
 *
 *  A conferência que importa: a soma dos votos de um cargo é o comparecimento
 *  daquela urna. Quem aparece vota uma vez por cargo, em alguém, em branco ou
 *  nulo. Se isso não fecha, a leitura está errada — e o resultado não vai à tela. */
export function conferir(boletim) {
  const problemas = [];
  for (const [codigoEleicao, cargos] of Object.entries(boletim.eleicoes)) {
    for (const cargo of cargos) {
      const total = cargo.votos.reduce((soma, voto) => soma + voto.quantidade, 0);
      if (total !== cargo.comparecimento) {
        problemas.push(`eleição ${codigoEleicao}, cargo ${cargo.codigo}: votos somam `
          + `${total} e o comparecimento declarado é ${cargo.comparecimento}`);
      }
      for (const voto of cargo.votos) {
        if (voto.tipo.startsWith("desconhecido")) {
          problemas.push(`cargo ${cargo.codigo}: tipo de voto ${voto.tipo}`);
        }
        if (voto.tipo === "nominal" && voto.numero === null) {
          problemas.push(`cargo ${cargo.codigo}: voto nominal sem número`);
        }
      }
    }
  }
  return problemas;
}

/* A sequência de decisão: endereço em texto → local de votação.
 *
 * É a mesma de `consulta_regiao.py`, que é a especificação do projeto. Qualquer
 * mudança aqui precisa valer lá também, e vice-versa:
 *
 *   0. número vazio, não numérico ou zero conta como "sem número" (no CNEFE,
 *      zero é a marca de S/N);
 *   1. rua de região única responde na hora;
 *   2. bairro restringe as regiões candidatas;
 *   3. número escolhe entre as candidatas, pelo índice de trechos;
 *   4. sem número, responde a de maior peso, marcada como provável. */

export const normalizar = s => (s ?? "").toString().toUpperCase()
  .normalize("NFD").replace(/[̀-ͯ]/g, "").trim();

export function opcoesDaRua(rua) {
  return rua.regioes ? rua.regioes.map(([r, f]) => [r, f])
                     : [[rua.regiao_provavel, rua.confianca ?? 1]];
}

export function opcoesDoBairro(rua, bairro) {
  const alvo = normalizar(bairro);
  for (const [nome, regioes] of (rua.bairros ?? [])) {
    if (normalizar(nome) === alvo) return regioes.map(([r, f]) => [r, f]);
  }
  return null;
}

export function bairrosDaRua(rua) {
  return (rua.bairros ?? []).map(([nome, regioes]) => ({ nome, regioes: regioes.length }));
}

function buscarNoTrecho(trechos, numero, candidatas) {
  if (!trechos || !trechos.length) return null;
  const inicios = trechos.map(t => t[0]);
  let pos = 0;
  while (pos + 1 < inicios.length && inicios[pos + 1] <= numero) pos++;
  if (candidatas.has(trechos[pos][1])) return [trechos[pos][1], inicios[pos] === numero];
  // O trecho encontrado é de outro pedaço da rua (outro bairro): caminha para
  // os lados até o primeiro compatível com as candidatas.
  for (let passo = 1; passo < trechos.length; passo++) {
    for (const i of [pos - passo, pos + passo]) {
      if (i >= 0 && i < trechos.length && candidatas.has(trechos[i][1])) return [trechos[i][1], false];
    }
  }
  return null;
}

export function resolver(rua, numero, bairro) {
  numero = numero && numero > 0 ? numero : null;
  const saida = { bairroIgnorado: false, conflito: false };

  if (rua.n_regioes === 1) {
    const opcoes = opcoesDaRua(rua);
    return { ...saida, id: opcoes[0][0], confianca: "exata", opcoes };
  }

  let opcoes = null;
  if (bairro) {
    opcoes = opcoesDoBairro(rua, bairro);
    if (opcoes === null) saida.bairroIgnorado = true;
  }
  if (opcoes === null) opcoes = opcoesDaRua(rua);

  const candidatas = new Set(opcoes.map(o => o[0]));
  if (candidatas.size === 1) return { ...saida, id: opcoes[0][0], confianca: "exata", opcoes };

  if (numero !== null) {
    const achado = buscarNoTrecho(rua.trechos, numero, candidatas);
    if (achado) {
      return { ...saida, id: achado[0], confianca: achado[1] ? "exata" : "interpolada", opcoes };
    }
    saida.conflito = Boolean(bairro && !saida.bairroIgnorado);
  }
  return { ...saida, id: opcoes[0][0], confianca: "provavel", opcoes };
}

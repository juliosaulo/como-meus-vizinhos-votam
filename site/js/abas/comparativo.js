/* Aba Comparativo: o local diante do município, do estado e do Brasil.
 *
 * Os universos são diferentes, e a página diz isso: o resultado do local vem da
 * base espacializada; município, estado e Brasil vêm da apuração completa,
 * inclusive os locais sem coordenada. É por isso que essas três linhas
 * reproduzem o resultado oficial, que o leitor pode conferir em outra fonte. */

import { corPorPosicao, pct } from "../formato.js";

const OUTROS = "Outros";

/** Alinha um território à lista de candidatos escolhida, somando o resto em "Outros". */
function fatias(candidatos, nomes) {
  const por = new Map((candidatos ?? []).map(c => [c.nome, c]));
  const principais = nomes.map(nome => ({
    nome, partido: por.get(nome)?.partido ?? null, pct: por.get(nome)?.pct ?? 0,
  }));
  const resto = (candidatos ?? []).filter(c => !nomes.includes(c.nome))
    .reduce((s, c) => s + c.pct, 0);
  return [...principais, { nome: OUTROS, partido: null, pct: Math.max(resto, 0) }];
}

function barra(rotulo, partes) {
  return `
    <div class="linha-territorio">
      <span class="rot">${rotulo}</span>
      <span class="empilhada">
        ${partes.filter(p => p.pct > 0).map((p, i) => `
          <span style="width:${p.pct}%;background:${corPorPosicao(i, p.nome)}"
                title="${p.nome}: ${pct(p.pct)}">${p.pct >= 8 ? pct(p.pct) : ""}</span>`).join("")}
      </span>
    </div>`;
}

export function render(estado) {
  const { resultados, ano, turno, agregados, municipio, ufNome } = estado;
  const local = resultados?.presidente?.[ano]?.[turno];
  if (!local) return '<p class="vazio">Sem resultado deste ano no local.</p>';
  if (!agregados?.municipio) {
    return `<p class="vazio">Os agregados por município, estado e Brasil não estão disponíveis
      nesta cópia dos dados.</p>`;
  }

  const nomes = local.slice(0, 3).map(c => c.nome);
  const doNivel = dados => dados?.presidente?.[ano]?.[turno];
  const noLocal = fatias(local, nomes);
  const noMunicipio = fatias(doNivel(agregados.municipio), nomes);

  const territorios = [
    ["Local", noLocal],
    [municipio?.nome ?? "Município", noMunicipio],
    [ufNome ?? "Estado", fatias(doNivel(agregados.uf), nomes)],
    ["Brasil", fatias(doNivel(agregados.brasil), nomes)],
  ];

  return `
    <div class="legenda">
      ${nomes.map((nome, i) => `
        <span><span class="bolinha" style="background:${corPorPosicao(i, nome)}"></span>
        ${nome}</span>`).join("")}
      <span><span class="bolinha" style="background:#c3ccd9"></span> Outros</span>
    </div>
    ${territorios.map(([rotulo, partes]) => barra(rotulo, partes)).join("")}

    <div class="diferencas">
      <div class="cabeca"><span class="rotulo">Diferença em relação ao município</span></div>
      ${nomes.map((nome, i) => {
        const dif = noLocal[i].pct - noMunicipio[i].pct;
        return `
          <div class="diferenca">
            <span><span class="bolinha" style="background:${corPorPosicao(i, nome)}"></span>
              ${nome}</span>
            <span class="${dif >= 0 ? "mais" : "menos"}">${dif >= 0 ? "+" : "−"}${
              Math.abs(dif).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
            } p.p.</span>
          </div>`;
      }).join("")}
    </div>
    <p class="nota">Município, estado e Brasil vêm da apuração completa do TSE, incluindo os locais
    de votação sem coordenada; a linha do local mostra apenas as urnas dele.</p>`;
}

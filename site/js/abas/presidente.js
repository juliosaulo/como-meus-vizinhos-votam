/* Aba Presidente: o resultado apurado no local selecionado. */

import { comOutros, tabelaCandidatos, blocoTotais, num } from "../formato.js";

export function render(estado) {
  const { resultados, ano, turno } = estado;
  const candidatos = resultados?.presidente?.[ano]?.[turno];
  if (!candidatos) return semDados(ano);

  const validos = candidatos.reduce((s, c) => s + c.votos, 0);
  const naoNominal = resultados?.nao_nominal?.presidente?.[ano]?.[turno];

  return `
    ${tabelaCandidatos(comOutros(candidatos))}
    ${blocoTotais(validos, naoNominal)}
    <p class="nota">Percentual sobre o voto válido — ${num(validos)} votos nominais apurados neste
    local. Brancos e nulos são contados à parte, sobre o total de comparecimento.</p>`;
}

function semDados(ano) {
  return `
    <div class="em-breve">
      <div class="icone" aria-hidden="true">▦</div>
      <h3>Resultados de ${ano} em breve</h3>
      <p>Os dados aparecerão aqui depois que o TSE publicar a votação por seção desta eleição.</p>
    </div>`;
}

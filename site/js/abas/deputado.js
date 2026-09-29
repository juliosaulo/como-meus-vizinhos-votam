/* Aba Deputado federal: os mais votados no local.
 *
 * A base publica os três primeiros de cada local — o suficiente para mostrar
 * quem puxa voto ali, sem transformar a página numa lista de centenas de nomes. */

import { tabelaCandidatos, num } from "../formato.js";

export function render(estado) {
  const { resultados, ano } = estado;
  const candidatos = resultados?.deputado_federal?.[ano]?.["1"];
  if (!candidatos) {
    return `
      <div class="em-breve">
        <div class="icone" aria-hidden="true">▦</div>
        <h3>Sem dados de ${ano}</h3>
        <p>Este local não tem votação de deputado federal apurada neste ano.</p>
      </div>`;
  }

  const total = resultados?.total_votos?.deputado_federal?.[ano]?.["1"];

  // Sem o bloco de válidos, brancos e nulos: aqui só aparecem três dos
  // centenas de candidatos, e o total não é a soma do que está na tela.
  return `
    ${tabelaCandidatos(candidatos)}
    <p class="nota">Os três candidatos mais votados neste local em ${ano}${
      total ? `, de ${num(total)} votos apurados` : ""}. O percentual de cada um é sobre o voto
    válido do local; os demais candidatos votados aqui não são publicados.</p>`;
}

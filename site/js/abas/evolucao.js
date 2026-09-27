/* Aba Evolução: os anos lado a lado, no mesmo local de votação.
 *
 * Quando um local deixou de existir, os votos dele foram atribuídos ao mais
 * próximo — o que torna a comparação entre anos aproximada nesses casos, e está
 * descrito em LIMITACOES.md. */

import { corPorPosicao, pct, num } from "../formato.js";

const ROTULO_TURNO = { "1": "1º turno", "2": "2º turno" };

/* No celular só cabe um turno por ano na tela: o escolhido no seletor, ou o
 * único que o ano tiver. No desktop os turnos ficam todos à vista. */
function cartaoAno(ano, porTurno, turnoCelular) {
  return `
    <div class="ano-card">
      <h3>${ano}</h3>
      ${Object.keys(porTurno).sort().map(t => `
        <div class="turno-bloco ${t !== turnoCelular && porTurno[turnoCelular] ? "so-desktop" : ""}">
        <div class="turno rotulo">${ROTULO_TURNO[t] ?? `${t}º turno`}</div>
        ${porTurno[t].slice(0, 2).map((c, i) => `
          <div class="linha">
            <div class="linha-topo">
              <span class="nome">${c.nome}</span>
              <span class="valor">${pct(c.pct)}</span>
            </div>
            <div class="trilho"><span style="width:${Math.max(c.pct, 1.5)}%;
              background:${corPorPosicao(i, c.nome)}"></span></div>
            <div class="partido">${num(c.votos)} votos</div>
          </div>`).join("")}
        </div>
      `).join("")}
    </div>`;
}

export function render(estado) {
  const presidente = estado.resultados?.presidente ?? {};
  const anos = Object.keys(presidente).sort();
  if (!anos.length) return '<p class="vazio">Sem histórico neste local.</p>';

  const emBreve = estado.anoFuturo ? `
    <div class="ano-card futuro">
      <h3>${estado.anoFuturo}</h3>
      <p class="rotulo" style="text-align:center">Em breve</p>
      <p class="vazio" style="text-align:center;margin-top:24px">Os resultados aparecerão aqui
      depois da publicação pelo TSE.</p>
    </div>` : "";

  const ultimoTurno = turnos => turnos["2"] ?? turnos["1"];
  const antes = ultimoTurno(presidente[anos[0]])?.[0];
  const agora = ultimoTurno(presidente[anos[anos.length - 1]])?.[0];
  const mudou = antes && agora && antes.nome !== agora.nome;

  return `
    <div class="evolucao">
      ${anos.map(ano => cartaoAno(ano, presidente[ano], estado.turno)).join("")}
      ${emBreve}
    </div>
    ${antes && agora ? `
      <div class="aviso">
        <span aria-hidden="true">▲</span>
        <span>De ${anos[0]} a ${anos[anos.length - 1]}, a liderança neste local
        ${mudou ? `mudou de <strong>${antes.nome}</strong> para <strong>${agora.nome}</strong>`
                : `permaneceu com <strong>${agora.nome}</strong>`}.</span>
      </div>` : ""}`;
}

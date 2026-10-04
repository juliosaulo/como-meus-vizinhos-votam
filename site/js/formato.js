/* Formatação e paleta.
 *
 * As barras usam uma única família de azuis, escurecendo conforme a posição, em
 * vez de cor por partido: a página é sobre um lugar, não sobre siglas, e cor
 * partidária num ranking sugere uma leitura que o dado não sustenta. */

const ESCALA = ["#1b4ea0", "#2f6fd0", "#5b93e0", "#8fb4e8", "#b9d1f0"];
const NEUTRO = "#c3ccd9";

export const corPorPosicao = (i, nome) =>
  (nome === "Outros" ? NEUTRO : ESCALA[Math.min(i, ESCALA.length - 1)]);

export const num = n => (n ?? 0).toLocaleString("pt-BR");
export const pct = n => `${(n ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;

/* O nome vai inteiro, como o TSE publica. Abreviar por conta própria — primeiro
 * e último, por exemplo — descaracteriza justamente os nomes mais conhecidos, e
 * pode mudar a pessoa que o leitor entende estar vendo. */

/** Top N candidatos, com o resto somado em "Outros". */
export function comOutros(candidatos, n = 5) {
  if (candidatos.length <= n + 1) return candidatos.map(c => ({ ...c }));
  const resto = candidatos.slice(n);
  return [...candidatos.slice(0, n).map(c => ({ ...c })), {
    nome: "Outros", partido: null,
    votos: resto.reduce((s, c) => s + c.votos, 0),
    pct: resto.reduce((s, c) => s + c.pct, 0),
  }];
}

export function tabelaCandidatos(candidatos) {
  const maior = Math.max(...candidatos.map(c => c.pct), 1);
  return `
    <table class="tabela">
      <thead>
        <tr>
          <th class="n">#</th><th>Candidato</th><th></th>
          <th class="n">Percentual</th><th class="n">Votos</th>
        </tr>
      </thead>
      <tbody>
        ${candidatos.map((c, i) => `
          <tr>
            <td class="pos">${i + 1}</td>
            <td>
              <div class="candidato">${c.nome}</div>
              <div class="partido">${c.partido ?? "—"}</div>
            </td>
            <td>
              <div class="trilho"><span style="width:${Math.max(c.pct / maior * 100, 1.5)}%;
                background:${corPorPosicao(i, c.nome)}"></span></div>
            </td>
            <td class="pct">${pct(c.pct)}</td>
            <td class="votos">${num(c.votos)} votos</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

/** Os totais do local. O último item é a abstenção; durante a apuração quem
 *  chama troca por "Total de eleitores", porque com parte das urnas processadas
 *  a abstenção calculada seria enorme e falsa. */
export function blocoTotais(validos, naoNominal, ultimo, rotuloDoUltimo = "Abstenção") {
  const itens = [
    ["▤", "Votos válidos", validos],
    ["◻", "Brancos", naoNominal?.branco?.votos ?? 0],
    ["⊗", "Nulos", naoNominal?.nulo?.votos ?? 0],
  ];
  // Só entra quando o eleitorado daquele local é conhecido: sem ele, a conta
  // não existe, e um zero ali seria lido como "ninguém faltou".
  if (ultimo != null) itens.push(["◌", rotuloDoUltimo, ultimo]);
  return `
    <div class="totais">
      ${itens.map(([icone, rotulo, valor]) => `
        <div class="total">
          <span class="icone" aria-hidden="true">${icone}</span>
          <span><span class="rotulo">${rotulo}</span><span class="valor">${num(valor)}</span></span>
        </div>`).join("")}
    </div>`;
}

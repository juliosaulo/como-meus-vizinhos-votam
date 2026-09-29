/* Orquestra a página: filtros, busca, lista de locais, abas e mapa.
 *
 * A regra do fluxo: pedir o mínimo. A rua basta em três de cada quatro casos; o
 * bairro só aparece quando a rua é ambígua e passa por mais de um bairro; o
 * número só quando o bairro ainda não resolveu. */

import * as dados from "./dados.js";
import { resolver, normalizar } from "./consulta.js";
import { mostrar as mostrarNoMapa } from "./mapa.js";
import { num } from "./formato.js";
import * as abaPresidente from "./abas/presidente.js";
import * as abaComparativo from "./abas/comparativo.js";
import * as abaEvolucao from "./abas/evolucao.js";
import * as abaDeputado from "./abas/deputado.js";

const $ = id => document.getElementById(id);
const celular = () => matchMedia("(max-width: 720px)").matches;

const estado = {
  uf: null, ufNome: null, municipio: null,
  municipios: null, ruas: null, regioes: null,
  rua: null, bairro: null, numero: null,
  achado: null, regiaoEscolhida: null,
  aba: "presidente", ano: null, turno: null,
  metadados: null, anoFuturo: null,
  agregados: { municipio: null, uf: null, brasil: null },
};

const ABAS = [
  ["presidente", "Presidente", abaPresidente, "Resultados por<br>local de votação"],
  ["comparativo", "Comparativo", abaComparativo, "Local diante<br>do entorno"],
  ["evolucao", "Evolução", abaEvolucao, "Eleição após<br>eleição"],
  ["deputado", "Dep. federal", abaDeputado, "Mais votados<br>neste local"],
];

// ---------------------------------------------------------------- início
async function iniciar() {
  try {
    const [lista, meta] = await Promise.all([dados.ufs(), dados.metadados()]);
    $("uf").innerHTML = '<option value="">Escolha…</option>' +
      lista.map(u => `<option value="${u.sigla}">${u.nome}</option>`).join("");
    estado.metadados = meta;
    const anos = Object.keys(meta?.eleicoes?.presidente ?? {}).map(Number);
    if (anos.length) estado.anoFuturo = Math.max(...anos) + 4;
    if (meta) {
      $("trilhaFonte").textContent =
        `Fontes: TSE e IBGE · malha ${meta.ano_referencia_malha} · ` +
        `${meta.regioes.toLocaleString("pt-BR")} locais`;
    }
  } catch (erro) {
    $("principal").innerHTML = `<p class="erro">Não consegui ler os dados publicados
      (${erro.message}). Sirva a pasta do projeto por HTTP e confira o caminho em
      <code>js/dados.js</code>.</p>`;
    return;
  }
  desenhar();
  await aplicarParametrosDaUrl();
}

/* Campo de digitar com lista de sugestões — o mesmo comportamento para cidade
 * e para rua. Cidade virou campo de digitar porque o menu nativo do celular,
 * com centenas de opções, é difícil de percorrer. */
function ligarAutocomplete({ campo, lista, itens, linha, aoEscolher, vazio, aoSair }) {
  let achados = [];

  campo.addEventListener("input", () => {
    const termo = normalizar(campo.value);
    const todos = itens();
    if (!todos || termo.length < 2) { lista.hidden = true; return; }
    achados = todos.filter(i => i._n.includes(termo)).slice(0, 20);
    lista.innerHTML = achados.length
      ? achados.map((item, i) => `<li><button data-i="${i}">${linha(item)}</button></li>`).join("")
      : `<li class="vazio" style="padding:10px">${vazio}</li>`;
    lista.hidden = false;
  });

  // O clique na sugestão tem de valer mais que a saída do campo.
  lista.addEventListener("mousedown", e => e.preventDefault());
  lista.addEventListener("click", e => {
    const botao = e.target.closest("button[data-i]");
    if (!botao) return;
    lista.hidden = true;
    aoEscolher(achados[Number(botao.dataset.i)]);
  });

  campo.addEventListener("blur", () => { lista.hidden = true; aoSair?.(); });
  campo.addEventListener("focus", () => campo.select());
  campo.addEventListener("keydown", e => { if (e.key === "Escape") lista.hidden = true; });
}

async function selecionarUf(sigla) {
  $("uf").value = sigla ?? "";
  estado.uf = sigla || null;
  estado.ufNome = $("uf").selectedOptions[0]?.textContent ?? null;
  limparMunicipio();
  const campo = $("municipio");
  campo.value = "";
  estado.municipios = null;
  if (!estado.uf) {
    campo.disabled = true; campo.placeholder = "Escolha o estado antes";
    return;
  }
  campo.disabled = true; campo.placeholder = "Carregando as cidades…";
  const municipios = await dados.municipios(estado.uf);
  estado.municipios = municipios.map(m => ({ ...m, _n: normalizar(m.nome) }));
  campo.disabled = false;
  campo.placeholder = "Digite o nome da cidade";
}

async function selecionarMunicipio(cd) {
  const achado = estado.municipios?.find(m => m.cd === cd);
  limparMunicipio();
  if (!cd || !achado) { $("municipio").value = ""; return; }
  $("municipio").value = achado.nome;
  estado.municipio = { cd, nome: achado.nome };
  $("rua").disabled = true;
  $("rua").placeholder = "Carregando as ruas…";
  try {
    const [ruas, regioes] = await Promise.all([dados.ruas(cd), dados.regioes(cd)]);
    estado.ruas = ruas.ruas.map(r => ({ ...r, _n: normalizar(r.nome) }));
    estado.regioes = regioes.regioes;
    $("rua").disabled = false;
    $("rua").placeholder = "Digite o nome da rua";
    $("rua").focus();
  } catch (erro) {
    $("rua").placeholder = "Digite o nome da rua";
    $("principal").innerHTML = `<p class="erro">Este município não está nesta cópia dos dados
      (${erro.message}).</p>`;
  }
  desenhar();
}

$("uf").addEventListener("change", e => selecionarUf(e.target.value));

ligarAutocomplete({
  campo: $("municipio"), lista: $("sugestoesMunicipio"),
  itens: () => estado.municipios,
  linha: m => m.nome,
  vazio: "Nenhuma cidade com esse nome neste estado.",
  aoEscolher: m => selecionarMunicipio(m.cd),
  // Texto digitado sem escolha não vale: o campo volta a mostrar a cidade em uso.
  aoSair: () => { $("municipio").value = estado.municipio?.nome ?? ""; },
});

/** Permite abrir a página já com um endereço: ?uf=RO&municipio=1100015&rua=...&numero=... */
async function aplicarParametrosDaUrl() {
  const p = new URLSearchParams(location.search);
  if (!p.get("uf")) return;
  await selecionarUf(p.get("uf"));
  if (!p.get("municipio")) return;
  await selecionarMunicipio(p.get("municipio"));
  if (!p.get("rua") || !estado.ruas) return;
  const alvo = normalizar(p.get("rua"));
  const rua = estado.ruas.find(r => r._n === alvo) ?? estado.ruas.find(r => r._n.includes(alvo));
  if (!rua) return;
  escolherRua(rua.nome);
  if (p.get("bairro")) { estado.bairro = p.get("bairro"); }
  if (p.get("numero")) { estado.numero = Number(p.get("numero")); $("numero").value = p.get("numero"); }
  if (p.get("aba")) { estado.aba = p.get("aba"); await carregarAgregados(); }
  desenhar();
}

function limparMunicipio() {
  Object.assign(estado, {
    municipio: null, ruas: null, regioes: null, rua: null, bairro: null, numero: null,
    achado: null, regiaoEscolhida: null, ano: null, turno: null,
    agregados: { municipio: null, uf: null, brasil: null },
  });
  $("rua").value = ""; $("rua").disabled = true;
  $("sugestoes").hidden = true;
  desenhar();
}

// ---------------------------------------------------------------- busca da rua
$("rua").addEventListener("input", e => { $("limparRua").hidden = !e.target.value; });

ligarAutocomplete({
  campo: $("rua"), lista: $("sugestoes"),
  itens: () => estado.ruas,
  linha: r => `${r.nome}
    <span class="qtd">· ${r.n_regioes === 1 ? "1 local" : `${r.n_regioes} locais`}</span>`,
  vazio: "Nenhuma rua com esse nome neste município.",
  aoEscolher: r => escolherRua(r.nome),
});

$("limparRua").addEventListener("click", limparRua);
$("limparFiltros").addEventListener("click", limparRua);
$("trocarRua").addEventListener("click", limparRua);

/** Volta ao ponto de escolher outra rua, mantendo estado e cidade. */
function limparRua() {
  $("rua").value = "";
  $("limparRua").hidden = true;
  $("numero").value = "";
  $("sugestoes").hidden = true;
  Object.assign(estado, { rua: null, bairro: null, numero: null, achado: null, regiaoEscolhida: null });
  desenhar();
  $("rua").focus();
}

// Sombra no cabeçalho fixo assim que a página sai do topo.
addEventListener("scroll", () => {
  document.querySelector(".barra-topo").classList.toggle("rolado", scrollY > 8);
}, { passive: true });

function escolherRua(nome) {
  estado.rua = estado.ruas.find(r => r.nome === nome) ?? null;
  estado.bairro = null;
  estado.numero = null;
  estado.regiaoEscolhida = null;
  $("rua").value = nome;
  $("limparRua").hidden = false;
  $("sugestoes").hidden = true;
  desenhar();
}

$("bairro").addEventListener("change", e => {
  estado.bairro = e.target.value || null;
  estado.numero = null;
  $("numero").value = "";
  estado.regiaoEscolhida = null;
  desenhar();
});

$("numero").addEventListener("input", e => {
  estado.numero = e.target.value ? Number(e.target.value) : null;
  estado.regiaoEscolhida = null;
  desenhar();
});

// ---------------------------------------------------------------- desenho
function desenhar() {
  estado.achado = estado.rua ? resolver(estado.rua, estado.numero, estado.bairro) : null;

  atualizarFiltros();
  atualizarTrilha();
  atualizarResumo();
  $("principal").innerHTML = estado.rua ? painelComRua() : painelInicial();
  ligarEventosDoPainel();
  atualizarLateral();

  // No celular, com o local definido, os filtros recolhem e o resultado cabe
  // numa tela só, a partir do topo — para ser lido e fotografado sem rolar.
  // Quem está digitando o número continua no campo; ele recolhe ao sair dele.
  const antes = document.body.classList.contains("com-local");
  const agora = localDefinido();
  document.body.classList.toggle("com-local", agora);
  if (!antes && agora && celular() && document.activeElement !== $("numero")) {
    document.activeElement?.blur();
    scrollTo(0, 0);
  }
}

function atualizarFiltros() {
  const rua = estado.rua;
  const bairros = rua?.bairros ?? [];
  const mostrarBairro = Boolean(rua && bairros.length);
  $("campoBairro").hidden = !mostrarBairro;
  if (mostrarBairro && $("bairro").dataset.rua !== rua.nome) {
    $("bairro").dataset.rua = rua.nome;
    $("bairro").innerHTML = '<option value="">Todos</option>' +
      bairros.map(([nome]) => `<option value="${nome.replace(/"/g, "&quot;")}">${nome}</option>`).join("");
  }
  if (mostrarBairro) $("bairro").value = estado.bairro ?? "";
  $("campoNumero").hidden = !(rua && estado.achado.opcoes.length > 1);

  $("limparFiltros").hidden = !rua;

  const pill = $("contador");
  if (!rua) {
    pill.className = "pill-status neutro";
    pill.textContent = estado.municipio ? "Comece escolhendo sua rua" : "Escolha o estado e a cidade";
    return;
  }
  const definido = localDefinido();
  pill.className = definido ? "pill-status" : "pill-status neutro";
  pill.textContent = definido
    ? "✓  1 local encontrado"
    : `${estado.achado.opcoes.length} locais possíveis`;
}

function atualizarTrilha() {
  const partes = ["Brasil"];
  if (estado.ufNome) partes.push(estado.ufNome);
  if (estado.municipio) partes.push(estado.municipio.nome);
  if (estado.rua) partes.push(estado.rua.nome);
  $("trilha").innerHTML = partes.map(p => `<span>${p}</span>`).join("");
}

/** Há um local definido? Ou sobrou um só, ou o número/bairro resolveu, ou o
 *  usuário escolheu um na lista. Enquanto não houver, a página não mostra
 *  resultado nenhum — mostrar o "mais provável" seria dar como certo o que
 *  ainda está em aberto. */
function localDefinido() {
  const achado = estado.achado;
  if (!achado) return false;
  return estado.regiaoEscolhida != null
      || achado.opcoes.length === 1
      || achado.confianca !== "provavel";
}

function atualizarResumo() {
  const definido = localDefinido();
  const id = estado.regiaoEscolhida ?? estado.achado?.id;
  const regiao = definido && id != null ? estado.regioes?.[id] : null;

  $("resumo").hidden = !definido;
  $("alertaRefinar").hidden = !(estado.rua && !definido);

  if (estado.rua && !definido) {
    const n = estado.achado.opcoes.length;
    $("alertaTitulo").textContent = `${n} locais de votação possíveis nesta rua`;
    $("alertaTexto").textContent = (estado.rua.bairros?.length && !estado.bairro)
      ? "Refine pelo bairro e/ou pelo número da casa, ou escolha um dos locais na lista abaixo."
      : "Informe o número da casa, ou escolha um dos locais na lista abaixo.";
  }

  $("resumoRua").textContent = estado.rua?.nome ?? "—";
  $("resumoLocal").textContent = regiao?.local ?? "—";
  $("resumoMunicipio").textContent = estado.municipio
    ? `${estado.municipio.nome} – ${estado.uf}` : "—";
}

function painelInicial() {
  const passos = [
    ["Escolha o estado", "Selecione o estado onde você quer ver os resultados."],
    ["Digite a cidade", "Comece a digitar o nome da cidade e escolha na lista."],
    ["Digite a rua", "Digite o nome da rua onde você mora ou tem interesse."],
    ["Veja ou refine os locais", "Se aparecer mais de um local, escolha o bairro ou informe o número."],
  ];
  return `
    <div class="bloco bloco-centrado">
      <div class="bloco-titulo"><h2>Como funciona</h2></div>
      <div class="passos">
        ${passos.map(([titulo, texto], i) => `
          <div class="passo"><span class="n">${i + 1}</span>
            <span><strong>${titulo}</strong><span>${texto}</span></span></div>`).join("")}
      </div>
      <p class="nota-miuda">Você não precisa informar o número da casa logo de início — só pedimos
      mais detalhes se a rua atender mais de um local de votação.</p>
    </div>`;
}

function painelComRua() {
  const achado = estado.achado;
  const id = estado.regiaoEscolhida ?? achado.id;
  // Com o local definido, a página mostra só o resultado dele: a lista já
  // cumpriu o papel, e mantê-la ali disputa atenção com o que a pessoa veio
  // ver. Para trocar de local, é mudar o bairro/número ou limpar os filtros.
  return localDefinido() ? blocoResultados(id) : blocoEscolha(achado, id);
}

function blocoEscolha(achado, idAtual) {
  const resolvido = achado.confianca !== "provavel";
  const bairros = estado.rua.bairros ?? [];

  return `
    <div class="bloco">
      <div class="bloco-titulo">
        <h2>${resolvido ? "Outros locais desta rua" : "Escolha o local de votação"}</h2>
        <span class="col-titulo">${achado.opcoes.length} locais<br>nesta rua</span>
      </div>

      ${resolvido ? `<p class="vazio">O filtro identificou um local; os demais desta rua ficam
        abaixo, com a proporção de endereços que cada um atende.</p>` : ""}

      ${bairros.length ? `
        <div style="margin-top:18px"><span class="rotulo">Desempate por bairro ou localidade</span>
        <div class="baloes">
          ${bairros.map(([nome, regioes]) => `
            <button class="balao ${normalizar(nome) === normalizar(estado.bairro ?? "") ? "ativo" : ""}"
                    data-bairro="${nome.replace(/"/g, "&quot;")}">${nome}
              <small>${regioes.length === 1 ? "1 local" : `${regioes.length} locais`}</small>
            </button>`).join("")}
          ${estado.bairro ? '<button class="balao" data-bairro="">limpar</button>' : ""}
        </div></div>` : ""}

      ${achado.bairroIgnorado ? '<div class="aviso"><span>⚠</span><span>O bairro informado não existe nesta rua e foi ignorado.</span></div>' : ""}
      ${achado.conflito ? '<div class="aviso"><span>⚠</span><span>O número informado não existe no trecho deste bairro e foi ignorado.</span></div>' : ""}

      <ul class="locais">
        ${achado.opcoes.map(([id, fracao], i) => {
          const r = estado.regioes[id] ?? {};
          return `<li><button class="local ${id === idAtual ? "ativo" : ""}" data-regiao="${id}">
            <span class="ordem">${i + 1}</span>
            <span><span class="nome">${r.local ?? `região ${id}`}</span><br>
              <span class="endereco">${r.endereco ?? ""}</span></span>
            <span class="share">${(fracao * 100).toFixed(0)}%<small>dos endereços</small></span>
          </button></li>`;
        }).join("")}
      </ul>
    </div>`;
}

function blocoResultados(id) {
  const regiao = estado.regioes[id];
  if (!regiao) return '<p class="vazio">Região sem dados publicados.</p>';

  const resultados = regiao.resultados ?? {};
  const anos = Object.keys(resultados.presidente ?? {}).sort();
  if (!estado.ano || !anos.includes(estado.ano)) estado.ano = anos[anos.length - 1];
  const turnos = Object.keys(resultados.presidente?.[estado.ano] ?? {}).sort();
  if (!estado.turno || !turnos.includes(estado.turno)) estado.turno = turnos[turnos.length - 1];

  const aba = ABAS.find(([chave]) => chave === estado.aba) ?? ABAS[0];
  const mostraTurno = estado.aba === "presidente" || estado.aba === "comparativo";
  const contexto = {
    resultados, ano: estado.ano, turno: estado.turno, anoFuturo: estado.anoFuturo,
    agregados: estado.agregados, municipio: estado.municipio, ufNome: estado.ufNome,
  };

  return `
    <div class="bloco">
      <div class="bloco-titulo">
        <h2>${aba[1]}</h2>
        <span class="col-titulo">${aba[3]}</span>
      </div>

      <div class="abas">
        ${ABAS.map(([chave, rotulo]) => `
          <button class="aba ${chave === estado.aba ? "ativa" : ""}" data-aba="${chave}">${rotulo}</button>`).join("")}
      </div>

      ${estado.aba === "evolucao" ? (turnos.length > 1 ? `
        <div class="seletores so-celular">
          <span class="grupo">
            ${turnos.map(t => `<button class="opcao ${t === estado.turno ? "ativa" : ""}" data-turno="${t}">${t}º turno</button>`).join("")}
          </span>
        </div>` : "") : `
        <div class="seletores">
          <div class="seletor">
            <span class="rotulo">Ano da eleição</span>
            <span class="grupo">
              ${anos.map(a => `<button class="opcao ${a === estado.ano ? "ativa" : ""}" data-ano="${a}">${a}</button>`).join("")}
              ${estado.anoFuturo ? `<button class="opcao" disabled>${estado.anoFuturo}<small>em breve</small></button>` : ""}
            </span>
          </div>
          ${mostraTurno && turnos.length > 1 ? `
            <div class="seletor">
              <span class="rotulo">Turno</span>
              <span class="grupo">
                ${turnos.map(t => `<button class="opcao ${t === estado.turno ? "ativa" : ""}" data-turno="${t}">${t}º turno</button>`).join("")}
              </span>
            </div>` : ""}
        </div>`}

      ${aba[2].render(contexto)}
    </div>`;
}

function ligarEventosDoPainel() {
  const painel = $("principal");
  painel.querySelectorAll("[data-bairro]").forEach(b => b.addEventListener("click", () => {
    estado.bairro = b.dataset.bairro || null;
    estado.numero = null;
    $("numero").value = "";
    estado.regiaoEscolhida = null;
    desenhar();
  }));
  painel.querySelectorAll("[data-regiao]").forEach(b => b.addEventListener("click", () => {
    estado.regiaoEscolhida = Number(b.dataset.regiao);
    desenhar();
  }));
  painel.querySelectorAll("[data-aba]").forEach(b => b.addEventListener("click", async () => {
    estado.aba = b.dataset.aba;
    if (estado.aba === "comparativo") await carregarAgregados();
    desenhar();
  }));
  painel.querySelectorAll("[data-ano]").forEach(b => b.addEventListener("click", () => {
    estado.ano = b.dataset.ano; estado.turno = null; desenhar();
  }));
  painel.querySelectorAll("[data-turno]").forEach(b => b.addEventListener("click", () => {
    estado.turno = b.dataset.turno; desenhar();
  }));
}

async function carregarAgregados() {
  if (estado.agregados.municipio || !estado.municipio) return;
  const [municipio, uf, brasil] = await Promise.all([
    dados.agregadoMunicipio(estado.municipio.cd),
    dados.agregadoUf(estado.uf),
    dados.agregadoBrasil(),
  ]);
  estado.agregados = { municipio, uf, brasil };
}

function atualizarLateral() {
  const id = estado.regiaoEscolhida ?? estado.achado?.id;
  const regiao = localDefinido() && id != null ? estado.regioes?.[id] : null;
  $("blocoMapa").hidden = !regiao;
  document.querySelector(".colunas").classList.toggle("sem-lateral", !regiao);
  if (!regiao) return;

  const anos = Object.keys(regiao.resultados?.presidente ?? {}).sort();
  const ultimo = anos[anos.length - 1];
  const turnos = Object.keys(regiao.resultados?.presidente?.[ultimo] ?? {}).sort();
  const candidatos = regiao.resultados?.presidente?.[ultimo]?.[turnos[turnos.length - 1]] ?? [];
  const votos = candidatos.reduce((s, c) => s + c.votos, 0);

  const linhas = [
    ["▣", "Nome", regiao.local],
    ["◉", "Endereço", regiao.endereco ?? "—"],
  ];
  if (votos) linhas.push(["▤", `Votos em ${ultimo}`, `${num(votos)} votos para presidente`]);
  if (regiao.outros_locais?.length) {
    linhas.push(["⋮", "Mesmo ponto", regiao.outros_locais.join(", ")]);
  }

  $("fichaLocal").innerHTML = linhas.map(([icone, rotulo, valor]) => `
    <div class="ficha-linha">
      <span class="icone" aria-hidden="true">${icone}</span>
      <span class="rotulo">${rotulo}</span>
      <span class="valor">${valor}</span>
    </div>`).join("");
  mostrarNoMapa(regiao);
}

iniciar();

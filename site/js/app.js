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
  modo: "rua", uf: null, ufNome: null, municipio: null,
  municipios: null, ruas: null, regioes: null, locais: null,
  rua: null, bairro: null, numero: null,
  achado: null, regiaoEscolhida: null,
  aba: "presidente", ano: null, turno: null, ufsLista: null,
  metadados: null, anoFuturo: null,
  agregados: { municipio: null, uf: null, brasil: null },
};

const ABAS = [
  ["presidente", "Presidente", abaPresidente],
  ["comparativo", "Comparativo", abaComparativo],
  ["evolucao", "Evolução", abaEvolucao],
  ["deputado", "Dep. federal", abaDeputado],
];

// ---------------------------------------------------------------- início
async function iniciar() {
  try {
    const [lista, meta] = await Promise.all([dados.ufs(), dados.metadados()]);
    $("uf").innerHTML = '<option value="">Escolha…</option>' +
      lista.map(u => `<option value="${u.sigla}">${u.nome}</option>`).join("");
    estado.metadados = meta;
    estado.ufsLista = lista;
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
  if (!await entrarPorLink()) await aplicarParametrosDaUrl();
}

/* ---------------------------------------------------------------- link compartilhado
 * Endereço de um local: /l/{municipio}/{zona-local}. O servidor devolve a mesma
 * página com as meta tags da prévia; aqui o site abre direto o resultado, sem
 * passar por estado, cidade e rua. */
const CAMINHO_LINK = /\/l\/(\d{7})\/(\d{1,4}-\d{1,5})\/?$/;

// Enquanto o site não tiver mexido no endereço, o que está na barra é de quem
// chegou: reescrever ali apagaria o link que a pessoa acabou de abrir.
let enderecoNosso = false;

/** Abre o resultado de um local pela chave pública dele. */
async function entrarPorLocal(cdMunicipio, idPublico) {
  const uf = estado.ufsLista?.find(u => u.cd === cdMunicipio.slice(0, 2))?.sigla;
  if (!uf) return false;
  await selecionarUf(uf);
  await selecionarMunicipio(cdMunicipio);
  const entrada = Object.entries(estado.regioes ?? {}).find(([, r]) => r.id === idPublico);
  if (!entrada) return false;
  estado.modo = "local";
  $("local").value = entrada[1].local;
  abrirResultado(Number(entrada[0]));
  return true;
}

async function entrarPorLink() {
  const params = new URLSearchParams(location.search);
  // `?municipio=&id=` é o mesmo caminho, útil onde não há reescrita de endereço.
  const doCaminho = CAMINHO_LINK.exec(location.pathname);
  const cd = doCaminho?.[1] ?? params.get("municipio");
  const id = doCaminho?.[2] ?? params.get("id");
  if (!cd || !id || !/^\d{1,4}-\d{1,5}$/.test(id)) return false;

  const abriu = await entrarPorLocal(cd, id);
  enderecoNosso = abriu;
  // Quem pesquisou e recarregou a página não recebeu link de ninguém: o estado
  // do histórico sobrevive ao F5 e distingue os dois casos.
  if (abriu && !history.state?.buscou) document.body.classList.add("via-link");
  return abriu;
}

/** Endereço compartilhável do local em exibição, ou null se não houver. */
function enderecoDoLocal() {
  const id = estado.regiaoEscolhida ?? estado.achado?.id;
  const publico = id != null ? estado.regioes?.[id]?.id : null;
  if (!publico || !estado.municipio) return null;
  return `/l/${estado.municipio.cd}/${publico}`;
}

/* O endereço na barra do navegador vira o do local assim que há resultado:
 * copiar o endereço passa a gerar um link compartilhável. Em desenvolvimento a
 * página não fica na raiz do domínio, e aí não há o que reescrever. */
function atualizarEndereco() {
  const naRaiz = location.pathname === "/" || CAMINHO_LINK.test(location.pathname);
  if (!naRaiz || (!enderecoNosso && !localDefinido())) return;
  const alvo = localDefinido() ? enderecoDoLocal() : "/";
  if (!alvo || location.pathname === alvo) return;
  enderecoNosso = true;
  history.replaceState({ buscou: true }, "", alvo);
}

async function compartilhar(botao) {
  const caminho = enderecoDoLocal();
  if (!caminho) return;
  const url = new URL(caminho, location.origin).href;
  const id = estado.regiaoEscolhida ?? estado.achado?.id;
  const nome = estado.regioes?.[id]?.local ?? "este local de votação";
  const texto = `Como votou ${nome}, em ${estado.municipio.nome} – ${estado.uf}`;

  // A folha de compartilhamento nativa só vale a pena onde ela é o caminho
  // normal: no celular. O Windows também expõe `navigator.share`, mas abre um
  // painel que quase ninguém usa — e quem clica aqui, no computador, espera o
  // link na área de transferência.
  if (navigator.share && matchMedia("(pointer: coarse)").matches) {
    try {
      await navigator.share({ title: "Como meus vizinhos votam?", text: texto, url });
      return;
    } catch (erro) {
      if (erro?.name === "AbortError") return;  // a pessoa fechou a folha de compartilhar
    }
  }
  avisarNoBotao(botao, await copiar(url) ? "Link copiado" : "Não consegui copiar");
}

/** Copia para a área de transferência, pelo caminho moderno ou pelo antigo.
 *  Navegador dentro de aplicativo costuma bloquear o primeiro e aceitar o
 *  segundo, então vale tentar os dois antes de desistir. */
async function copiar(url) {
  try {
    await navigator.clipboard.writeText(url);
    return true;
  } catch { /* segue para o modo antigo */ }

  const campo = document.createElement("textarea");
  campo.value = url;
  campo.readOnly = true;
  campo.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(campo);
  campo.select();
  let copiou = false;
  try { copiou = document.execCommand("copy"); } catch { copiou = false; }
  campo.remove();
  return copiou;
}

function avisarNoBotao(botao, aviso) {
  const original = botao.dataset.rotulo ?? botao.textContent;
  botao.dataset.rotulo = original;
  botao.textContent = aviso;
  clearTimeout(botao._volta);
  botao._volta = setTimeout(() => { botao.textContent = original; }, 2500);
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
    // A busca pelo nome do local procura no nome e no endereço: quem lembra
    // "escola do bairro tal" acha pelos dois.
    estado.locais = Object.entries(regioes.regioes).map(([id, r]) => ({
      id: Number(id), nome: r.local, endereco: r.endereco,
      _n: normalizar(`${r.local} ${r.endereco ?? ""}`),
    })).sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR"));
    $("rua").disabled = false;
    $("rua").placeholder = "Digite o nome da rua";
    $("local").disabled = false;
    campoDaBusca().focus();
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
  document.body.classList.remove("via-link");
  Object.assign(estado, {
    municipio: null, ruas: null, regioes: null, locais: null, rua: null, bairro: null, numero: null,
    achado: null, regiaoEscolhida: null, ano: null, turno: null,
    agregados: { municipio: null, uf: null, brasil: null },
  });
  $("rua").value = ""; $("rua").disabled = true;
  $("local").value = ""; $("local").disabled = true;
  $("sugestoes").hidden = true;
  $("sugestoesLocal").hidden = true;
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

// ---------------------------------------------------------------- busca do local
ligarAutocomplete({
  campo: $("local"), lista: $("sugestoesLocal"),
  itens: () => estado.locais,
  linha: l => `${l.nome}
    <span class="qtd">· ${l.endereco ?? ""}</span>`,
  vazio: "Nenhum local de votação com esse nome neste município.",
  aoEscolher: l => { $("local").value = l.nome; abrirResultado(l.id); },
});

const campoDaBusca = () => (estado.modo === "local" ? $("local") : $("rua"));

/** Mostra o resultado de um local, sem passar pelo endereço. É o mesmo caminho
 *  da busca por nome e da entrada por link compartilhado. */
function abrirResultado(id) {
  estado.regiaoEscolhida = Number(id);
  estado.rua = null;
  estado.bairro = null;
  estado.numero = null;
  $("sugestoesLocal").hidden = true;
  desenhar();
}

$("modoBusca").addEventListener("click", e => {
  const botao = e.target.closest("[data-modo]");
  if (!botao || botao.dataset.modo === estado.modo) return;
  estado.modo = botao.dataset.modo;
  limparBusca();
});

$("limparRua").addEventListener("click", limparBusca);
$("limparFiltros").addEventListener("click", limparBusca);
$("trocarRua").addEventListener("click", limparBusca);

/** Volta ao ponto de escolher outra rua ou outro local, mantendo estado e cidade. */
function limparBusca() {
  document.body.classList.remove("via-link");
  $("rua").value = "";
  $("local").value = "";
  $("limparRua").hidden = true;
  $("numero").value = "";
  $("sugestoes").hidden = true;
  $("sugestoesLocal").hidden = true;
  Object.assign(estado, { rua: null, bairro: null, numero: null, achado: null, regiaoEscolhida: null });
  desenhar();
  if (!campoDaBusca().disabled) campoDaBusca().focus();
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
  atualizarEndereco();
  atualizarTrilha();
  atualizarResumo();
  $("principal").innerHTML = painelPrincipal();
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
  const porLocal = estado.modo === "local";
  $("campoRua").hidden = porLocal;
  $("campoLocal").hidden = !porLocal;
  document.querySelectorAll("#modoBusca [data-modo]").forEach(b =>
    b.classList.toggle("ativa", b.dataset.modo === estado.modo));

  const rua = estado.rua;
  const bairros = rua?.bairros ?? [];
  const mostrarBairro = Boolean(rua && bairros.length && !porLocal);
  $("campoBairro").hidden = !mostrarBairro;
  if (mostrarBairro && $("bairro").dataset.rua !== rua.nome) {
    $("bairro").dataset.rua = rua.nome;
    $("bairro").innerHTML = '<option value="">Todos</option>' +
      bairros.map(([nome]) => `<option value="${nome.replace(/"/g, "&quot;")}">${nome}</option>`).join("");
  }
  if (mostrarBairro) $("bairro").value = estado.bairro ?? "";
  $("campoNumero").hidden = porLocal || !(rua && estado.achado.opcoes.length > 1);

  const definido = localDefinido();
  $("limparFiltros").hidden = !(rua || definido);

  const pill = $("contador");
  pill.className = definido ? "pill-status" : "pill-status neutro";
  if (definido) { pill.textContent = "✓  1 local encontrado"; return; }
  if (!estado.municipio) { pill.textContent = "Escolha o estado e a cidade"; return; }
  if (porLocal || !rua) {
    pill.textContent = porLocal ? "Comece digitando o local" : "Comece escolhendo sua rua";
    return;
  }
  pill.textContent = `${estado.achado.opcoes.length} locais possíveis`;
}

function atualizarTrilha() {
  const partes = ["Brasil"];
  if (estado.ufNome) partes.push(estado.ufNome);
  if (estado.municipio) partes.push(estado.municipio.nome);
  if (estado.rua) partes.push(estado.rua.nome);
  else if (estado.regiaoEscolhida != null) {
    partes.push(estado.regioes?.[estado.regiaoEscolhida]?.local ?? "local de votação");
  }
  $("trilha").innerHTML = partes.map(p => `<span>${p}</span>`).join("");
}

/** Há um local definido? Ou sobrou um só, ou o número/bairro resolveu, ou o
 *  usuário escolheu um na lista. Enquanto não houver, a página não mostra
 *  resultado nenhum — mostrar o "mais provável" seria dar como certo o que
 *  ainda está em aberto. */
function localDefinido() {
  if (estado.modo === "local") return estado.regiaoEscolhida != null;
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

  // Sem rua na busca por local, a faixa não tem logradouro para mostrar.
  const porLocal = estado.modo === "local";
  $("resumoItemRua").hidden = porLocal;
  $("rotuloLocal").textContent = porLocal ? "Local de votação" : "Local de votação mais próximo";
  $("trocarRua").textContent = porLocal ? "Trocar local" : "Trocar rua";
  $("resumoRua").textContent = estado.rua?.nome ?? "—";
  $("resumoLocal").textContent = regiao?.local ?? "—";
  $("resumoMunicipio").textContent = estado.municipio
    ? `${estado.municipio.nome} – ${estado.uf}` : "—";
}

function painelInicial() {
  const porLocal = estado.modo === "local";
  const passos = [
    ["Escolha o estado", "Selecione o estado onde você quer ver os resultados."],
    ["Digite a cidade", "Comece a digitar o nome da cidade e escolha na lista."],
    porLocal
      ? ["Digite o local de votação", "O nome da escola, do colégio ou do prédio onde se vota."]
      : ["Digite a rua", "Digite o nome da rua onde você mora ou tem interesse."],
    porLocal
      ? ["Veja o resultado", "Os votos apurados naquele local, eleição a eleição."]
      : ["Veja ou refine os locais", "Se aparecer mais de um local, escolha o bairro ou informe o número."],
  ];
  return `
    <div class="bloco bloco-centrado">
      <div class="bloco-titulo"><h2>Como funciona</h2></div>
      <div class="passos">
        ${passos.map(([titulo, texto], i) => `
          <div class="passo"><span class="n">${i + 1}</span>
            <span><strong>${titulo}</strong><span>${texto}</span></span></div>`).join("")}
      </div>
      <p class="nota-miuda">${porLocal
        ? "A busca procura pelo nome do local e também pelo endereço dele."
        : `Você não precisa informar o número da casa logo de início — só pedimos
           mais detalhes se a rua atender mais de um local de votação.`}</p>
    </div>`;
}

function painelPrincipal() {
  if (estado.modo === "local") {
    return estado.regiaoEscolhida != null ? blocoResultados(estado.regiaoEscolhida) : painelInicial();
  }
  return estado.rua ? painelComRua() : painelInicial();
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
    // Pode não existir: nem todo local está na base de eleitorado do TSE.
    abstencao: regiao.abstencao, eleitorado: regiao.eleitorado,
  };

  return `
    <div class="bloco">
      <div class="bloco-titulo">
        <h2>${aba[1]}</h2>
        ${botoesDoResultado()}
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

/** Compartilhar — e, para quem chegou por um link, a chamada de ver o seu.
 *  Ficam no lugar do rótulo da coluna: é onde o olho já está quando o
 *  resultado aparece. */
function botoesDoResultado() {
  if (!enderecoDoLocal()) return "";
  return `
    <div class="acoes-resultado">
      <a class="so-link botao-veja" href="/">Veja o seu</a>
      <button class="botao-compartilhar" data-compartilhar>Compartilhar</button>
    </div>`;
}

function ligarEventosDoPainel() {
  const painel = $("principal");
  painel.querySelectorAll("[data-compartilhar]").forEach(b =>
    b.addEventListener("click", () => compartilhar(b)));
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

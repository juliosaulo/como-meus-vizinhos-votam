/* Acesso aos arquivos publicados.
 *
 * O site é estático: tudo vem de JSON servidos por caminho relativo, e nada do
 * que o usuário digita sai do navegador. Trocar `BASE` é o que move o site de
 * desenvolvimento para produção. */

/* Resolvido a partir do próprio módulo, e não da página: assim qualquer página
 * (o site, uma página de teste) enxerga os dados no mesmo lugar. Em produção,
 * troque por uma URL absoluta — é a única linha que muda. */
export const BASE = new URL("../../publicado/", import.meta.url).href;

/* Os JSON ficam guardados pelo navegador por muito tempo e não têm o nome
 * versionado. Sem carimbar a versão na URL, quem já visitou o site continuaria
 * lendo o dado antigo depois de uma publicação nova — inclusive sem os campos
 * que o site passou a usar, o que quebra em silêncio.
 *
 * `metadados.json` é pequeno, vem sem cache, e o `gerado_em` dele carimba todo
 * o resto: publicação nova muda a data, a data muda as URLs, e o navegador
 * busca tudo de novo sozinho. */
const metadadosPromessa = fetch(`${BASE}metadados.json`, { cache: "no-store" })
  .then(r => (r.ok ? r.json() : null))
  .catch(() => null);

const versao = metadadosPromessa.then(m => m?.gerado_em ?? "");

const cache = new Map();

async function carregar(caminho, { opcional = false } = {}) {
  if (!cache.has(caminho)) {
    cache.set(caminho, (async () => {
      const v = await versao;
      const url = `${BASE}${caminho}${v ? `?v=${encodeURIComponent(v)}` : ""}`;
      try {
        const r = await fetch(url);
        if (!r.ok) {
          if (opcional) return null;
          throw new Error(`não encontrei ${caminho}`);
        }
        return await r.json();
      } catch (erro) {
        if (opcional) return null;
        throw erro;
      }
    })());
  }
  return cache.get(caminho);
}

export const ufs = () => carregar("ufs.json");
export const municipios = uf => carregar(`municipios/${uf}.json`);
export const ruas = cd => carregar(`ruas/${cd}.json`);
export const regioes = cd => carregar(`regioes/${cd}.json`);
export const bairros = cd => carregar(`bairros/${cd}.json`, { opcional: true });
export const metadados = () => metadadosPromessa;
export const agregadoMunicipio = cd => carregar(`agregados/municipios/${cd}.json`, { opcional: true });
export const agregadoUf = uf => carregar(`agregados/ufs/${uf}.json`, { opcional: true });
export const agregadoBrasil = () => carregar("agregados/brasil.json", { opcional: true });

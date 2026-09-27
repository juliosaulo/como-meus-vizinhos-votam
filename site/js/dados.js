/* Acesso aos arquivos publicados.
 *
 * O site é estático: tudo vem de JSON servidos por caminho relativo, e nada do
 * que o usuário digita sai do navegador. Trocar `BASE` é o que move o site de
 * desenvolvimento para produção. */

/* Resolvido a partir do próprio módulo, e não da página: assim qualquer página
 * (o site, uma página de teste) enxerga os dados no mesmo lugar. Em produção,
 * troque por uma URL absoluta — é a única linha que muda. */
export const BASE = new URL("../../publicado/", import.meta.url).href;

const cache = new Map();

async function carregar(caminho, { opcional = false } = {}) {
  if (!cache.has(caminho)) {
    cache.set(caminho, fetch(`${BASE}${caminho}`).then(r => {
      if (!r.ok) {
        if (opcional) return null;
        throw new Error(`não encontrei ${caminho}`);
      }
      return r.json();
    }).catch(erro => {
      if (opcional) return null;
      throw erro;
    }));
  }
  return cache.get(caminho);
}

export const ufs = () => carregar("ufs.json");
export const municipios = uf => carregar(`municipios/${uf}.json`);
export const ruas = cd => carregar(`ruas/${cd}.json`);
export const regioes = cd => carregar(`regioes/${cd}.json`);
export const bairros = cd => carregar(`bairros/${cd}.json`, { opcional: true });
export const metadados = () => carregar("metadados.json", { opcional: true });
export const agregadoMunicipio = cd => carregar(`agregados/municipios/${cd}.json`, { opcional: true });
export const agregadoUf = uf => carregar(`agregados/ufs/${uf}.json`, { opcional: true });
export const agregadoBrasil = () => carregar("agregados/brasil.json", { opcional: true });

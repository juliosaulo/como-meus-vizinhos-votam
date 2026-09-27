/* Mapa do local de votação: um pino, o do local selecionado.
 *
 * O projeto não desenha área nenhuma — a "região" é um ponto de votação, e a
 * área que ele atende é uma inferência. Marcar o ponto deixa isso visível, em
 * vez de escondê-lo atrás de uma mancha colorida. */

let mapa = null;
let pino = null;

function criar() {
  // No toque, arrastar o mapa prenderia a rolagem da página; o zoom fica nos botões.
  mapa = L.map("mapa", { scrollWheelZoom: false, zoomControl: true, dragging: !L.Browser.mobile });
  // Tiles da Stadia: o servidor do OSM proíbe uso pesado e bloqueia sites com
  // muito tráfego. A Stadia libera localhost sem cadastro; em produção o
  // domínio precisa estar registrado na conta (sem chave no código).
  L.tileLayer("https://tiles.stadiamaps.com/tiles/osm_bright/{z}/{x}/{y}{r}.png", {
    maxZoom: 20,
    attribution: '© <a href="https://stadiamaps.com/">Stadia Maps</a> · © <a href="https://openmaptiles.org/">OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(mapa);
  return mapa;
}

export function mostrar(regiao) {
  if (!regiao) return;
  if (!mapa) criar();
  const ponto = [regiao.lat, regiao.lon];
  if (pino) pino.remove();
  pino = L.marker(ponto, { alt: `Local de votação: ${regiao.local}` }).addTo(mapa)
    .bindPopup(`<strong>${regiao.local}</strong><br>${regiao.endereco ?? ""}`);
  mapa.setView(ponto, 16);
  // O contêiner muda de tamanho quando a coluna aparece; sem isso o Leaflet
  // desenha os tiles no lugar errado.
  setTimeout(() => mapa.invalidateSize(), 60);
}

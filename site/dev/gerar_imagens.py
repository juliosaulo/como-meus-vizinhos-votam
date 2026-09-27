"""Gera as imagens do site a partir das fontes em SVG/HTML.

    python site/dev/gerar_imagens.py

- img/og.png            1200×630, compartilhamento (Open Graph / Twitter), de dev/og.html
- favicon.ico           16, 32 e 48 px, de favicon.svg
- apple-touch-icon.png  180 px
- img/icone-192.png, img/icone-512.png   para o site.webmanifest

Precisa de playwright (com o Chromium instalado) e Pillow.
"""

from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

SITE = Path(__file__).resolve().parent.parent
SVG = SITE / "favicon.svg"


def icone(pagina, lado: int, destino: Path) -> None:
    pagina.set_viewport_size({"width": lado, "height": lado})
    pagina.set_content(f'<body style="margin:0"><img src="{SVG.as_uri()}" '
                       f'style="display:block;width:{lado}px;height:{lado}px"></body>')
    pagina.wait_for_load_state("load")
    pagina.screenshot(path=str(destino))


def main() -> None:
    (SITE / "img").mkdir(exist_ok=True)
    temporarios = []
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()

        pagina.set_viewport_size({"width": 1200, "height": 630})
        pagina.goto((SITE / "dev" / "og.html").as_uri())
        pagina.wait_for_load_state("load")
        pagina.screenshot(path=str(SITE / "img" / "og.png"))

        icone(pagina, 180, SITE / "apple-touch-icon.png")
        icone(pagina, 192, SITE / "img" / "icone-192.png")
        icone(pagina, 512, SITE / "img" / "icone-512.png")
        for lado in (16, 32, 48):
            destino = SITE / "dev" / f"_favicon-{lado}.png"
            icone(pagina, lado, destino)
            temporarios.append(destino)
        navegador.close()

    imagens = [Image.open(t) for t in temporarios]
    imagens[-1].save(SITE / "favicon.ico", sizes=[im.size for im in imagens], append_images=imagens[:-1])
    for im, t in zip(imagens, temporarios):
        im.close()
        t.unlink()
    print("imagens geradas em", SITE)


if __name__ == "__main__":
    main()

"""
Deriva el icono de la aplicación y la marca del logotipo original.

El original se versiona en `marca/` para que el proceso sea reproducible: si
mañana hay que regenerar en otro tamaño, se vuelve a ejecutar este script en
lugar de recortar a mano una imagen que nadie recuerda de dónde salió.

Pillow se usa **solo aquí**. Está en `requirements-build.txt`, junto a
PyInstaller, porque es una herramienta de preparación: ni el servidor ni el
ejecutable de escritorio la necesitan en ningún momento. Los archivos que
produce se versionan ya listos.

Uso:
    pip install -r requirements-build.txt
    python scripts/generar_iconos.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = RAIZ / "marca" / "rj-cars.jpg"
DESTINO = RAIZ / "apps/estacionamiento/static/estacionamiento"

# Recorte del original, medido sobre la imagen y no estimado a ojo: el
# logotipo ocupa 885 × 743 px centrado en un lienzo de 2048, y el cuadrado
# oscuro llega hasta los bordes —las esquinas de la imagen son blancas,
# fuera del redondeo—. Se toma un cuadrado centrado algo mayor que el
# logotipo, de modo que quede aire alrededor sin recoger ese blanco.
LADO_RECORTE = 1180
CENTRO = (1026, 1023)

# Radio de las esquinas, en proporción al lado. Se redondea aquí en lugar de
# confiar en el original porque el recorte es interior y sus esquinas son
# rectas. Android e iOS aplican además su propia máscara; el redondeo importa
# sobre todo en la pestaña del navegador, donde nadie lo enmascara.
PROPORCION_RADIO = 0.18

# 32 para la pestaña del navegador, 180 porque es lo que pide iOS al añadir a
# la pantalla de inicio, 192 para el manifiesto de Android y a la vez la marca
# que se incrusta en los boletos, y 512 para la pantalla de inicio de Android.
#
# Un solo juego de archivos para el icono y para la marca. Duplicar el mismo
# dibujo con dos nombres solo garantiza que algún día uno quede
# desactualizado, y en los boletos el de 192 basta de sobra: se muestra a unos
# 72 px, de modo que incluso en una pantalla de triple densidad sobran píxeles.
TAMANOS_ICONO = (32, 180, 192, 512)


def _recorte_cuadrado() -> Image.Image:
    original = Image.open(ORIGEN).convert("RGB")
    mitad = LADO_RECORTE // 2
    cx, cy = CENTRO
    return original.crop((cx - mitad, cy - mitad, cx + mitad, cy + mitad))


def _con_esquinas_redondeadas(imagen: Image.Image) -> Image.Image:
    """Aplica una máscara de esquinas redondeadas sobre canal alfa."""
    lado = imagen.size[0]
    mascara = Image.new("L", (lado, lado), 0)
    ImageDraw.Draw(mascara).rounded_rectangle(
        (0, 0, lado - 1, lado - 1), radius=int(lado * PROPORCION_RADIO), fill=255
    )

    resultado = imagen.convert("RGBA")
    resultado.putalpha(mascara)
    return resultado


def _escribir(imagen: Image.Image, lado: int, nombre: str) -> None:
    # LANCZOS: el logotipo tiene trazos finos y un remuestreo pobre los
    # convierte en un borrón a 32 px, que es justo donde más se nota.
    reducida = imagen.resize((lado, lado), Image.Resampling.LANCZOS)
    ruta = DESTINO / nombre
    _con_esquinas_redondeadas(reducida).save(ruta, "PNG", optimize=True)
    print(f"  {nombre}  ({ruta.stat().st_size // 1024} KB)")


def main() -> None:
    if not ORIGEN.exists():
        raise SystemExit(f"Falta el logotipo original en {ORIGEN}")

    DESTINO.mkdir(parents=True, exist_ok=True)
    recorte = _recorte_cuadrado()

    for lado in TAMANOS_ICONO:
        _escribir(recorte, lado, f"icono-{lado}.png")


if __name__ == "__main__":
    main()

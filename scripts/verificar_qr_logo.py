"""
Mide si el código QR con el logotipo al centro se sigue leyendo.

No es una prueba de la suite porque necesita dos lectores de QR que no son
dependencias del proyecto, ni deben serlo: solo hacen falta para tomar esta
medida. Se instalan aparte, en un directorio temporal:

    pip install --target /tmp/lectores zxing-cpp opencv-python-headless
    PYTHONPATH=/tmp/lectores DJANGO_SETTINGS_MODULE=config.settings \\
        DJANGO_SECRET_KEY=x python scripts/verificar_qr_logo.py

Compara el código anterior (nivel medio, sin logotipo) con el actual bajo 19
condiciones —giros, desenfoque, tamaño reducido y perspectiva— y con tres
longitudes de dirección. La geometría del logotipo sale de `apps.boletos.qr`,
de modo que mide lo que la caja dibuja, no una copia.

Úselo antes de cambiar `PROPORCION_LOGO` o `CORRECCION_CON_LOGO`: el
logotipo debe leerse en los mismos casos que el código sin él.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
django.setup()

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import zxingcpp  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from apps.boletos import qr  # noqa: E402

LOGO = Path(__file__).resolve().parent.parent / "apps/estacionamiento/static/estacionamiento/icono-192.png"
ESCALA = 6
DIRECCIONES = {
    "66 caracteres": "https://caja.rjcars.com.mx/t/60b86282-1bc8-4764-82c9-ac647d8d62d6/",
    "77 caracteres": "https://boletos.mi-estacionamiento.mx/t/60b86282-1bc8-4764-82c9-ac647d8d62d6/",
    "96 caracteres": "https://estacionamiento-ircars-production.up.railway.app/t/60b86282-1bc8-4764-82c9-ac647d8d62d6/",
}


def dibujar(url: str, con_logo: bool, logo_visible: bool = True):
    """Mapa de bits equivalente al SVG de `qr.svg_qr`."""
    nivel = qr.CORRECCION_CON_LOGO if con_logo else qr.CORRECCION
    simbolo = qr._simbolo(url, correccion=nivel)
    memoria = io.BytesIO()
    simbolo.save(memoria, kind="png", scale=ESCALA, border=qr.BORDE_SVG)
    imagen = Image.open(memoria).convert("RGB")
    if con_logo:
        modulos = simbolo.symbol_size(border=0)[0]
        lado = qr.modulos_del_logo(modulos)
        inicio = (qr.BORDE_SVG + (modulos - lado) // 2) * ESCALA
        ancho = lado * ESCALA
        ImageDraw.Draw(imagen).rounded_rectangle(
            [inicio, inicio, inicio + ancho - 1, inicio + ancho - 1], radius=ESCALA, fill="white"
        )
        if logo_visible:
            respiro = ESCALA // 2
            logo = Image.open(LOGO).convert("RGBA").resize(
                (ancho - 2 * respiro, ancho - 2 * respiro), Image.LANCZOS
            )
            imagen.paste(logo, (inicio + respiro, inicio + respiro), logo)
    matriz = cv2.cvtColor(np.array(imagen), cv2.COLOR_RGB2BGR)
    matriz = cv2.copyMakeBorder(matriz, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    return matriz, simbolo.designator


def condiciones(imagen):
    alto, ancho = imagen.shape[:2]
    for angulo in (5, 10, 15, 20, 25, 30, 35, 40):
        giro = cv2.getRotationMatrix2D((ancho / 2, alto / 2), angulo, 0.85)
        yield cv2.warpAffine(imagen, giro, (ancho, alto), borderValue=(255, 255, 255))
    for sigma in (1.5, 2.5, 3.5, 4.5):
        yield cv2.GaussianBlur(imagen, (0, 0), sigma)
    for lado in (260, 200, 160, 130):
        yield cv2.resize(imagen, (lado, lado), interpolation=cv2.INTER_AREA)
    for inclinacion in (0.08, 0.14, 0.20):
        m = ancho * inclinacion
        origen = np.float32([[0, 0], [ancho, 0], [ancho, alto], [0, alto]])
        destino = np.float32([[m, m * 0.5], [ancho - m * 0.3, 0], [ancho, alto], [m * 0.2, alto - m]])
        matriz = cv2.getPerspectiveTransform(origen, destino)
        yield cv2.warpPerspective(imagen, matriz, (ancho, alto), borderValue=(255, 255, 255))


def main() -> None:
    detector = cv2.QRCodeDetector()
    casos = [
        ("Anterior: nivel medio, sin logotipo", False, True),
        (f"Actual: nivel alto, logotipo {qr.PROPORCION_LOGO:.0%}", True, True),
        ("Actual con el logotipo sin cargar", True, False),
    ]
    for rotulo, url in DIRECCIONES.items():
        print(f"\n{rotulo}")
        for nombre, con_logo, visible in casos:
            imagen, version = dibujar(url, con_logo, visible)
            zx = cv = total = 0
            for variante in condiciones(imagen):
                total += 1
                zx += url in [r.text for r in zxingcpp.read_barcodes(variante)]
                cv += detector.detectAndDecode(variante)[0] == url
            print(f"  {nombre:<40} versión {version:<5} ZXing {zx:2}/{total}  OpenCV {cv:2}/{total}")


if __name__ == "__main__":
    main()

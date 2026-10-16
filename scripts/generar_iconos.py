"""
Genera el icono de la aplicación en SVG y PNG.

Se versiona el generador junto a los archivos que produce para que el icono
sea reproducible: si mañana cambia el color de marca, se ajusta aquí y se
vuelve a ejecutar, en lugar de editar binarios a mano o depender de una
herramienta externa que quizá ya no esté instalada.

No usa Pillow a propósito. El proyecto evita esa dependencia —engorda el
ejecutable de escritorio y añade una superficie de decodificación de imágenes
que no hace falta—, así que el PNG se escribe con `zlib` y `struct`, que son
de la biblioteca estándar.

Uso:
    python scripts/generar_iconos.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "apps/estacionamiento/static/estacionamiento"

# Azul de marca, el mismo `--acento` de la hoja de estilos.
FONDO = (27, 79, 145)
TINTA = (255, 255, 255)

# Muestreo por píxel para suavizar los bordes. Cuatro por eje —dieciséis
# muestras— basta: el icono se ve a 32 px y a 180 px, y por encima de ese
# valor la mejora deja de apreciarse mientras el tiempo de cálculo crece.
SUPERMUESTREO = 4


def _dentro_de_la_p(x: float, y: float) -> bool:
    """
    Indica si el punto cae dentro de la letra, en coordenadas de 0 a 1.

    La «P» se construye con geometría y no con una fuente: así el icono no
    depende de qué tipografías tenga instalada la máquina que lo genere, y se
    ve idéntico en cualquier parte.

    Son dos piezas: el asta vertical y el anillo del ojo, recortado a su mitad
    derecha para que no invada el asta.
    """
    # Asta vertical.
    if 0.30 <= x <= 0.42 and 0.18 <= y <= 0.82:
        return True

    # Ojo: anillo centrado a la derecha del asta, en su mitad superior.
    centro_x, centro_y = 0.42, 0.40
    radio_externo, radio_interno = 0.22, 0.095
    distancia = ((x - centro_x) ** 2 + (y - centro_y) ** 2) ** 0.5
    if x >= centro_x and radio_interno <= distancia <= radio_externo:
        return True

    return False


def _esquina_redondeada(x: float, y: float, radio: float = 0.18) -> bool:
    """
    Recorta el lienzo en un cuadrado de esquinas redondeadas.

    Solo las cuatro zonas de esquina necesitan comprobación: en el resto del
    cuadrado el punto siempre está dentro.
    """
    cx = radio if x < radio else (1 - radio if x > 1 - radio else None)
    cy = radio if y < radio else (1 - radio if y > 1 - radio else None)

    if cx is None or cy is None:
        return True

    return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= radio


def _pixel(x: float, y: float, lado_pixel: float) -> tuple[int, int, int, int]:
    """
    Color de un píxel, promediando las muestras para suavizar el borde.

    `lado_pixel` es el tamaño del píxel en el lienzo normalizado (1/lado). Las
    muestras se reparten **dentro** de él; tomar el paso sobre el lienzo
    completo haría que cada píxel promediara una cuarta parte de la imagen y
    el resultado sería un degradado, no un dibujo.
    """
    acumulado = [0.0, 0.0, 0.0, 0.0]
    paso = lado_pixel / SUPERMUESTREO

    for sy in range(SUPERMUESTREO):
        for sx in range(SUPERMUESTREO):
            mx = x + (sx + 0.5) * paso
            my = y + (sy + 0.5) * paso

            if not _esquina_redondeada(mx, my):
                continue  # Transparente fuera del cuadrado redondeado.

            color = TINTA if _dentro_de_la_p(mx, my) else FONDO
            acumulado[0] += color[0]
            acumulado[1] += color[1]
            acumulado[2] += color[2]
            acumulado[3] += 255

    muestras = SUPERMUESTREO**2
    if acumulado[3] == 0:
        return (0, 0, 0, 0)

    # El color se promedia solo entre las muestras opacas; de lo contrario el
    # borde se oscurecería al mezclarse con el negro de las transparentes.
    opacas = acumulado[3] / 255
    return (
        round(acumulado[0] / opacas),
        round(acumulado[1] / opacas),
        round(acumulado[2] / opacas),
        round(acumulado[3] / muestras),
    )


def _escribir_png(ruta: Path, lado: int) -> None:
    """Escribe un PNG RGBA sin dependencias externas."""
    filas = bytearray()
    for fila in range(lado):
        filas.append(0)  # Filtro «None» para esta línea.
        for columna in range(lado):
            filas.extend(_pixel(columna / lado, fila / lado, 1.0 / lado))

    def fragmento(tipo: bytes, datos: bytes) -> bytes:
        cuerpo = tipo + datos
        return (
            struct.pack(">I", len(datos))
            + cuerpo
            + struct.pack(">I", zlib.crc32(cuerpo) & 0xFFFFFFFF)
        )

    cabecera = struct.pack(">IIBBBBB", lado, lado, 8, 6, 0, 0, 0)
    ruta.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + fragmento(b"IHDR", cabecera)
        + fragmento(b"IDAT", zlib.compress(bytes(filas), 9))
        + fragmento(b"IEND", b"")
    )


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="18" fill="#1b4f91"/>
  <path d="M30 18 h12 v64 h-12 z" fill="#fff"/>
  <path d="M42 18 a22 22 0 0 1 0 44 v-12 a10 10 0 0 0 0-20 z" fill="#fff"/>
</svg>
"""


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)

    (DESTINO / "icono.svg").write_text(SVG, encoding="utf-8")
    print("  icono.svg")

    # 180 px lo pide iOS al añadir a la pantalla de inicio; 192 y 512 los
    # declara el manifiesto para Android.
    for lado in (180, 192, 512):
        _escribir_png(DESTINO / f"icono-{lado}.png", lado)
        print(f"  icono-{lado}.png")


if __name__ == "__main__":
    main()

"""
Generación del código QR que enlaza al boleto del cliente.

Se eligió **segno** y no `qrcode`, que es la opción más difundida, por una
razón de empaquetado: `qrcode` depende de Pillow para producir imágenes, y
Pillow añadiría decenas de megabytes a la imagen del servidor además de una
superficie de decodificación de imágenes que esta aplicación no necesita.
`segno` es Python puro, escribe PNG y SVG por sí mismo y pesa unos cientos de
kilobytes.

El PNG resultante de un QR de esta longitud ronda los 300 bytes, de modo que
puede incrustarse en la ventana de la caja o en el comprobante sin tocar disco.
"""
from __future__ import annotations

import io
from html import escape

from django.conf import settings

# Corrección de errores media: recupera hasta un 15 % del símbolo dañado. Es el
# punto adecuado para un boleto impreso en papel térmico, que se arruga, se
# dobla y se decolora. Un nivel mayor agrandaría el símbolo sin necesidad.
CORRECCION = "m"

# Con el logotipo al centro, el nivel sube a alto (recupera hasta un 30 %). El
# logotipo tapa módulos a propósito: el lector los da por dañados y los
# reconstruye con la redundancia. Con el nivel medio, el logotipo se comería
# casi todo el margen y bastaría una arruga para que el código dejara de
# leerse. El precio es un símbolo más denso (versión 8 en lugar de 5 para una
# dirección típica), que en pantalla sigue leyéndose sin problema.
CORRECCION_CON_LOGO = "h"

# Ancho del recuadro del logotipo respecto del símbolo: el 18 % del ancho,
# poco más del 3 % del área. No se eligió a ojo. Se midió con dos lectores
# (ZXing, el de la mayoría de los teléfonos Android, y OpenCV) sobre 19
# condiciones —giros, desenfoque, tamaño reducido, perspectiva— y con
# direcciones de 66 a 96 caracteres. Al 18 % el código se lee en los mismos
# casos que el anterior sin logotipo; al 22 % ya fallaban dos giros más. La
# diferencia la hace el recuadro oscuro del logotipo, no los módulos que tapa:
# con el logotipo sin cargar, el 22 % se leía igual que el 18 %.
PROPORCION_LOGO = 0.18

# Zona de silencio, en módulos. Es la que segno usa por omisión; se fija aquí
# porque las coordenadas del logotipo dependen de ella.
BORDE_SVG = 4


class QRNoDisponible(RuntimeError):
    """
    No se pudo generar el QR por falta de la biblioteca.

    Se distingue con un tipo propio para que la caja pueda seguir registrando
    entradas y mostrar un aviso concreto en lugar de fallar: el QR es una
    comodidad para el cliente, no un requisito para cobrar.
    """


def url_boleto(uuid_folio) -> str:
    """
    Dirección pública del boleto. Es el contenido exacto del QR.

    Se construye a partir de `IRCARS_URL_BOLETOS`, la misma variable que usa el
    publicador, de modo que el enlace impreso y el destino de la publicación no
    puedan apuntar a sitios distintos.
    """
    base = str(getattr(settings, "IRCARS_URL_BOLETOS", "")).rstrip("/")
    if not base:
        raise QRNoDisponible(
            "IRCARS_URL_BOLETOS no está configurada: no hay dirección que poner "
            "en el código QR."
        )
    return f"{base}/t/{uuid_folio}/"


def _simbolo(contenido: str, correccion: str = CORRECCION):
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise QRNoDisponible(
            "Falta la biblioteca «segno» para generar códigos QR. "
            "Instálela con: pip install segno"
        ) from exc

    return segno.make(contenido, error=correccion)


def modulos_del_logo(modulos_por_lado: int) -> int:
    """
    Lado del recuadro del logotipo, en módulos enteros.

    Se ajusta a la retícula para que el recuadro tape módulos completos: uno
    cortado a la mitad deja un borde gris que el lector puede tomar por oscuro
    o por claro según el ángulo. Y se fuerza la misma paridad que el símbolo
    —siempre impar— para que quede centrado exacto, con el mismo número de
    módulos a cada lado.
    """
    lado = round(modulos_por_lado * PROPORCION_LOGO)
    if (modulos_por_lado - lado) % 2:
        lado -= 1
    return max(lado, 1)


def svg_qr(contenido: str, escala: int = 5, logo_url: str | None = None) -> str:
    """
    QR como SVG en línea, para incrustar directamente en una plantilla HTML.

    En línea y no como archivo estático: evita una segunda petición al servidor
    y, sobre todo, mantiene el boleto en un único documento autocontenido que
    se imprime y se guarda sin depender de recursos externos.

    Con `logo_url`, el símbolo se genera con corrección alta y lleva el
    logotipo al centro, dentro del propio SVG: escala y se imprime junto con
    el código, sin depender de cómo la hoja de estilos coloque una imagen
    encima. Si el logotipo no llega a cargar, queda un recuadro blanco y el
    código se sigue leyendo: los módulos que tapa son los mismos.
    """
    if not logo_url:
        return _simbolo(contenido).svg_inline(scale=escala)

    simbolo = _simbolo(contenido, correccion=CORRECCION_CON_LOGO)
    modulos = simbolo.symbol_size(border=0)[0]
    lado_logo = modulos_del_logo(modulos)

    # Coordenadas en píxeles del propio SVG: segno dibuja cada módulo de
    # `escala` píxeles y deja `BORDE_SVG` módulos de zona de silencio.
    lado_total = (modulos + 2 * BORDE_SVG) * escala
    inicio = (BORDE_SVG + (modulos - lado_logo) // 2) * escala
    ancho = lado_logo * escala
    # El logotipo entra medio módulo hacia dentro del recuadro blanco: el
    # blanco a su alrededor lo separa de los módulos vecinos, que de otro modo
    # se leerían pegados a su canto oscuro.
    respiro = escala / 2

    marcado = simbolo.svg_inline(scale=escala, border=BORDE_SVG)
    # segno fija ancho y alto pero no `viewBox`: sin él, la hoja de estilos
    # que limita el ancho del código lo recortaba en lugar de reducirlo. Con
    # el nivel alto el símbolo pasa de 270 a 342 px a escala 6 y ya no cabía.
    marcado = marcado.replace(
        "<svg ", f'<svg viewBox="0 0 {lado_total} {lado_total}" ', 1
    )
    figura = (
        f'<rect x="{inicio}" y="{inicio}" width="{ancho}" height="{ancho}" '
        f'rx="{escala}" fill="#fff"/>'
        f'<image href="{escape(logo_url, quote=True)}" '
        f'x="{inicio + respiro}" y="{inicio + respiro}" '
        f'width="{ancho - 2 * respiro}" height="{ancho - 2 * respiro}" '
        f'preserveAspectRatio="xMidYMid meet" aria-hidden="true"/>'
    )
    return marcado.replace("</svg>", f"{figura}</svg>", 1)


def png_qr(contenido: str, escala: int = 7, borde: int = 2) -> bytes:
    """
    QR como PNG en memoria.

    Pensado para la ventana de la caja: Tk 8.6 muestra PNG de forma nativa en
    `tkinter.PhotoImage`, por lo que no hace falta Pillow ni un archivo
    temporal en disco.

    El borde —la «zona de silencio»— no es decorativo: sin él, muchos lectores
    no reconocen el símbolo contra un fondo contiguo.
    """
    memoria = io.BytesIO()
    _simbolo(contenido).save(memoria, kind="png", scale=escala, border=borde)
    return memoria.getvalue()

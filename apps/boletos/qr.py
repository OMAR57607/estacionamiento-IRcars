"""
Generación del código QR que enlaza al boleto del cliente.

Se eligió **segno** y no `qrcode`, que es la opción más difundida, por una
razón de empaquetado: `qrcode` depende de Pillow para producir imágenes, y
Pillow añade decenas de megabytes al ejecutable de PyInstaller además de una
superficie de decodificación de imágenes que esta aplicación no necesita.
`segno` es Python puro, escribe PNG y SVG por sí mismo y pesa unos cientos de
kilobytes.

El PNG resultante de un QR de esta longitud ronda los 300 bytes, de modo que
puede incrustarse en la ventana de la caja o en el comprobante sin tocar disco.
"""
from __future__ import annotations

import io

from django.conf import settings

# Corrección de errores media: recupera hasta un 15 % del símbolo dañado. Es el
# punto adecuado para un boleto impreso en papel térmico, que se arruga, se
# dobla y se decolora. Un nivel mayor agrandaría el símbolo sin necesidad.
CORRECCION = "m"


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


def _simbolo(contenido: str):
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise QRNoDisponible(
            "Falta la biblioteca «segno» para generar códigos QR. "
            "Instálela con: pip install segno"
        ) from exc

    return segno.make(contenido, error=CORRECCION)


def svg_qr(contenido: str, escala: int = 5) -> str:
    """
    QR como SVG en línea, para incrustar directamente en una plantilla HTML.

    En línea y no como archivo estático: evita una segunda petición al servidor
    y, sobre todo, mantiene el boleto en un único documento autocontenido que
    se imprime y se guarda sin depender de recursos externos.
    """
    return _simbolo(contenido).svg_inline(scale=escala)


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

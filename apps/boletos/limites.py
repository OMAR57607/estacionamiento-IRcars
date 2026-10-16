"""
Límite de tasa por dirección IP para las rutas públicas.

El boleto se sirve sin autenticación: es la única forma de que el cliente lo
abra con la cámara del teléfono sin registrarse. Eso obliga a poner el límite
en otra parte, porque una ruta pública sin tope permite dos abusos concretos:

1. **Sondeo de identificadores.** Aunque un UUID v4 no es adivinable por
   fuerza bruta —122 bits de aleatoriedad—, un sondeo sostenido consume CPU y
   conexiones de base de datos. Cortarlo es gratuito y evita el ruido.
2. **Agotamiento del servicio.** Dos procesos de Gunicorn atendiendo un
   estacionamiento no resisten un torrente de peticiones, y el servicio de
   boletos comparte infraestructura con la ingesta de la caja.

Limitación conocida y asumida: el contador vive en el caché de Django. Con el
caché en memoria local —el de omisión— cada proceso lleva su propia cuenta, de
modo que el límite efectivo se multiplica por el número de procesos. Para este
volumen es suficiente y no justifica añadir Redis; si algún día lo justifica,
basta con configurar `CACHES` y este módulo no cambia.
"""
from __future__ import annotations

import functools
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)

PREFIJO = "ircars:limite:"


def ip_cliente(request) -> str:
    """
    Dirección del solicitante, resistente a la suplantación de cabeceras.

    Hay dos despliegues posibles y cada uno exige mirar una cabecera distinta.

    **Detrás de Cloudflare.** La cadena es cliente → Cloudflare → Railway →
    aplicación, y `X-Forwarded-For` deja de servir: su entrada derecha pasa a
    ser la IP del **borde de Cloudflare**, no la del cliente. Usarla agruparía
    a todo el mundo en un puñado de contadores —el límite del formulario de
    acceso dejaría de ser por persona y podría dejar fuera al propio operador
    sin que nadie esté atacando nada—. Cloudflare resuelve esto añadiendo
    `CF-Connecting-IP` con la dirección real, y es la que se consulta.

    **Solo detrás del proxy de la plataforma.** Se toma la entrada más a la
    derecha de `X-Forwarded-For`, no la primera. El motivo suele pasarse por
    alto: un proxy añade la IP que él observa al final de la cabecera, de modo
    que la última entrada es la única que escribió infraestructura propia. Las
    anteriores pueden venir del cliente, y confiar en la primera permitiría
    eludir el límite rotando valores inventados.

    **Sin proxy alguno**, ninguna cabecera es de fiar y manda `REMOTE_ADDR`.

    Límite conocido y asumido: con `IRCARS_TRAS_CLOUDFLARE` activo, quien
    conozca la dirección de Railway puede saltarse Cloudflare y enviar un
    `CF-Connecting-IP` inventado para eludir el límite. Cerrarlo del todo no
    es cuestión de código sino de configuración —restringir el origen para que
    solo acepte tráfico de Cloudflare—, y mientras tanto el perjuicio de
    confiar en la cabecera es mucho menor que el de no hacerlo: sin ella el
    límite no protege a nadie y además estorba a los operadores legítimos.
    """
    if getattr(settings, "IRCARS_TRAS_CLOUDFLARE", False):
        del_borde = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        if del_borde:
            return del_borde
        # Ausente significa que esta petición no pasó por Cloudflare: alguien
        # llamó directamente a la dirección de la plataforma. Ahí la entrada
        # derecha de `X-Forwarded-For` sí es el cliente real, así que se sigue
        # con el camino de abajo en lugar de descartarla.

    if getattr(settings, "IRCARS_TRAS_PROXY", True):
        reenviadas = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if reenviadas:
            ultima = reenviadas.split(",")[-1].strip()
            if ultima:
                return ultima

    return request.META.get("REMOTE_ADDR", "") or "desconocida"


# Métodos que no modifican nada. Un límite pensado para intentos de acceso
# debe ignorarlos: cargar el formulario no es un intento.
METODOS_SEGUROS = ("GET", "HEAD", "OPTIONS")


def limitar_por_ip(
    peticiones: int,
    ventana_segundos: int,
    etiqueta: str,
    solo_escrituras: bool = False,
):
    """
    Limita a `peticiones` por `ventana_segundos` y dirección IP.

    Ventana fija, no deslizante: es menos precisa en los bordes y mucho más
    barata —una sola clave por IP y ventana—. Para contener abuso, la
    diferencia es irrelevante.

    `solo_escrituras` cuenta únicamente los métodos que modifican algo. Es lo
    que corresponde en un formulario de acceso: lo que hay que acotar son los
    intentos de contraseña, no las veces que alguien abre la página. Contando
    las lecturas, un operador que recargara la pantalla se quedaría fuera.
    """

    def decorador(vista):
        @functools.wraps(vista)
        def envoltura(request, *args, **kwargs):
            if solo_escrituras and request.method in METODOS_SEGUROS:
                return vista(request, *args, **kwargs)

            ip = ip_cliente(request)
            # La ventana forma parte de la clave: al cambiar de ventana la
            # clave cambia y el contador se reinicia solo, sin tarea de
            # limpieza ni expiraciones que coordinar.
            marca = int(request_timestamp() // ventana_segundos)
            clave = f"{PREFIJO}{etiqueta}:{ip}:{marca}"

            # `add` solo escribe si la clave no existe; crea el contador con su
            # expiración en una operación y evita la carrera entre dos
            # peticiones simultáneas de la misma IP.
            cache.add(clave, 0, ventana_segundos)
            try:
                conteo = cache.incr(clave)
            except ValueError:
                # La clave expiró entre el `add` y el `incr`. Es inocuo:
                # equivale a la primera petición de la ventana siguiente.
                conteo = 1

            if conteo > peticiones:
                logger.warning(
                    "Límite de tasa alcanzado | ruta=%s ip=%s conteo=%s",
                    etiqueta,
                    ip,
                    conteo,
                )
                respuesta = HttpResponse(
                    "Demasiadas consultas. Espere un momento e intente de nuevo.",
                    content_type="text/plain; charset=utf-8",
                    status=429,
                )
                respuesta["Retry-After"] = str(ventana_segundos)
                return respuesta

            return vista(request, *args, **kwargs)

        return envoltura

    return decorador


def request_timestamp() -> float:
    """
    Reloj de pared, en segundos, con el que se numera la ventana.

    Se usa el reloj de pared y no `time.monotonic` a propósito: la ventana
    debe ser la misma para todos los procesos que comparten el caché, y el
    reloj monótono de cada proceso parte de un origen distinto, de modo que
    cada uno numeraría ventanas diferentes y el límite dejaría de ser común.

    Un ajuste del reloj del sistema puede, como mucho, reiniciar o prolongar
    una ventana: una imprecisión aceptable para contener abuso.

    Está aislado en una función para poder sustituirlo en las pruebas sin
    tocar el reloj del sistema ni esperar en tiempo real.
    """
    return time.time()

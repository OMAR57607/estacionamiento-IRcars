"""
Controlador del boleto que abre el cliente.

Una sola superficie y una sola regla: `/t/<uuid>/` se lee desde el teléfono,
sin autenticación de ninguna clase, y es de **solo lectura, sin excepción**.
No existe ninguna ruta pública que escriba, de modo que un fallo en esta
página no puede alterar nada.

Hubo aquí una segunda superficie, la ingesta que escribía la caja local
contra un token compartido. Se retiró con la arquitectura que la justificaba:
hoy la caja vive en este mismo servicio y el boleto se construye leyendo el
folio.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_safe

from apps.estacionamiento import marca
from apps.estacionamiento.domain.exceptions import ErrorDominio
from apps.estacionamiento.domain.tarifas import cotizar, minutos_transcurridos

from .limites import limitar_por_ip
from .presentacion import BoletoVista, desde_folio

logger = logging.getLogger(__name__)

# ------------------------------------------------------- Consulta del cliente


def _vigencia_horas() -> int:
    return int(getattr(settings, "IRCARS_HORAS_VIGENCIA_BOLETO", 24))


def _esta_vencido(boleto: BoletoVista) -> bool:
    """
    Un boleto que ya terminó su ciclo deja de consultarse pasada su vigencia.

    No es limpieza, es reducción de exposición: una dirección pública que
    funciona para siempre se acumula en historiales, mensajes reenviados y
    copias de seguridad de terceros. Mientras el vehículo está dentro, el
    enlace es útil y permanece activo sin límite; cuando el cliente ya se fue,
    deja de serlo a las pocas horas.

    Se consulta `fin_de_ciclo` y no `salida_en` porque un boleto **cancelado**
    no tiene hora de salida —el vehículo no salió por caja— y con `salida_en`
    su página seguiría pública indefinidamente.
    """
    fin = boleto.fin_de_ciclo
    if fin is None:
        return False
    return timezone.now() - fin > timedelta(hours=_vigencia_horas())


def _blindar(respuesta: HttpResponse) -> HttpResponse:
    """
    Cabeceras obligatorias de toda respuesta pública del boleto.

    * `no-store`: el importe acumulado cambia cada minuto. Una copia en caché
      —del navegador o de un intermediario— mostraría una cifra vencida, y en
      un teléfono compartido dejaría el boleto visible para el siguiente.
    * `noindex`: ningún boleto debe terminar en un buscador. Basta con que un
      cliente comparta su enlace en un sitio público para que el rastreador lo
      siga.
    * `no-referrer`: la dirección **es** la credencial. Sin esta cabecera,
      cualquier salto desde la página la filtraría en la cabecera `Referer`.
    """
    respuesta["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    respuesta["Pragma"] = "no-cache"
    respuesta["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    respuesta["Referrer-Policy"] = "no-referrer"
    return respuesta


def _no_encontrado(request) -> HttpResponse:
    """
    Respuesta única para «no existe» y «ya venció».

    Deliberadamente indistinguibles: si el servicio contestara distinto a un
    identificador inexistente y a uno vencido, confirmaría cuáles existieron.
    """
    respuesta = render(
        request,
        "boletos/no_encontrado.html",
        {"nombre_comercial": _nombre_comercial()},
        status=404,
    )
    return _blindar(respuesta)


def _nombre_comercial() -> str:
    """
    Delega en `apps.estacionamiento.marca`: un solo origen para el nombre.

    Se conserva la función como envoltura, y no se llama a la del módulo de
    marca en cada punto de uso, para que esta vista siga sin depender de la
    forma en que se resuelve el nombre.
    """
    return marca.nombre_comercial()


def _calcular_presentacion(boleto: BoletoVista, ahora) -> dict:
    """
    Resuelve qué cifras mostrar según el estado del boleto.

    En un folio abierto el importe es **estimado al instante de la consulta** y
    se recalcula con el mismo motor que usa la caja. En uno liquidado se
    muestra lo efectivamente cobrado, que ya no depende del reloj.
    """
    if boleto.estado == "CERRADO":
        return {
            "minutos": boleto.minutos or 0,
            "importe": boleto.importe,
            "es_estimado": False,
            "cotizacion": None,
        }

    if boleto.estado == "CANCELADO":
        return {"minutos": None, "importe": None, "es_estimado": False, "cotizacion": None}

    try:
        minutos = minutos_transcurridos(boleto.entrada_en, ahora)
        cotizacion = cotizar(minutos, boleto.a_parametros())
    except ErrorDominio:
        # Un reloj desincronizado entre la caja y el servidor no debe producir
        # un error 500 en la cara del cliente: se muestra el boleto sin la
        # cifra estimada, que es lo único que no puede calcularse.
        logger.warning(
            "No se pudo estimar el importe del boleto %s.", boleto.uuid_folio
        )
        return {"minutos": None, "importe": None, "es_estimado": True, "cotizacion": None}

    return {
        "minutos": cotizacion.minutos,
        "importe": cotizacion.importe,
        "es_estimado": True,
        "cotizacion": cotizacion,
    }


def _resolver(uuid_folio) -> BoletoVista | None:
    """
    Busca el folio y lo traduce a la vista del cliente.

    El enmascarado de la placa ocurre al presentar, dentro de `desde_folio`:
    la base guarda la placa completa porque la caja la necesita, y lo que
    nunca sale de aquí es la placa entera.
    """
    from apps.estacionamiento.models import Folio

    folio = (
        Folio.objects.select_related("tarifa", "cajon")
        .filter(uuid=uuid_folio)
        .first()
    )
    return None if folio is None else desde_folio(folio)


# 60 consultas por minuto y por IP. Holgado para que un cliente refresque su
# boleto cuanto quiera, y suficiente para cortar un sondeo automatizado.
@require_safe
@limitar_por_ip(peticiones=60, ventana_segundos=60, etiqueta="boleto")
def boleto_publico(request, uuid_folio):
    """
    Página del boleto que abre el cliente al leer el código QR.

    Sin sesión, sin cookies, sin formularios y sin un solo recurso externo: la
    plantilla lleva sus estilos en línea. Un teléfono con mala señal en la
    rampa de salida debe poder abrirla en una sola petición.
    """
    boleto = _resolver(uuid_folio)
    if boleto is None or _esta_vencido(boleto):
        return _no_encontrado(request)

    ahora = timezone.now()
    contexto = {
        "boleto": boleto,
        "nombre_comercial": _nombre_comercial(),
        "consultado_en": ahora,
        **_calcular_presentacion(boleto, ahora),
    }
    return _blindar(render(request, "boletos/boleto.html", contexto))

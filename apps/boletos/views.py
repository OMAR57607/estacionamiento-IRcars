"""
Controladores del servicio de boletos.

Son dos superficies con públicos opuestos y, por tanto, con reglas opuestas:

* **Ingesta** (`/api/v1/boletos/`): la escribe la caja, autenticada con un
  token compartido. Es la única ruta de escritura de todo el servicio.
* **Consulta** (`/t/<uuid>/`): la lee el cliente desde su teléfono, sin
  autenticación de ninguna clase. Es de solo lectura, sin excepción.

Esa separación es la propiedad de seguridad central del servicio: **no existe
ninguna ruta pública que escriba**. Aunque la vista de consulta tuviera un
fallo, no hay nada que pueda alterar desde ella.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_safe
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.estacionamiento import marca
from apps.estacionamiento.domain.exceptions import ErrorDominio
from apps.estacionamiento.domain.tarifas import cotizar, minutos_transcurridos

from .limites import limitar_por_ip
from .models import BoletoPublicado
from .presentacion import BoletoVista, desde_folio, desde_publicado
from .serializers import BoletoIngestaSerializer

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------- Ingesta


class IngestaBoletosView(APIView):
    """
    Recibe la instantánea pública de un folio enviada por la caja.

    Autenticación por token compartido y no por usuario: quien llama es una
    máquina, no una persona, y no tiene sentido crear un usuario con sesión
    para una caseta. El token se compara con `secrets.compare_digest`, que
    tarda lo mismo con un token correcto que con uno casi correcto; una
    comparación con `==` revelaría, por el tiempo de respuesta, cuántos
    caracteres iniciales acertó quien lo intenta.

    La operación es **idempotente**: recibir dos veces la misma carga deja el
    mismo resultado. Esa garantía es la que permite al publicador reintentar
    sin llevar registro de lo entregado.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ingesta_boletos"

    def post(self, request):
        esperado = str(getattr(settings, "IRCARS_TOKEN_PUBLICACION", ""))
        if not esperado:
            # Sin token configurado la ruta no se abre «por omisión»: se cierra.
            # Un despliegue a medio configurar que aceptara cualquier carga
            # permitiría a cualquiera publicar boletos falsos.
            logger.error(
                "Ingesta de boletos invocada sin IRCARS_TOKEN_PUBLICACION configurado."
            )
            return Response(
                {"detalle": "El servicio de boletos no está configurado."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not self._token_valido(request, esperado):
            # Mensaje escueto a propósito: no se informa si el token era
            # inválido, estaba ausente o tenía mal formato.
            return Response(
                {"detalle": "Credenciales inválidas."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializador = BoletoIngestaSerializer(data=request.data)
        serializador.is_valid(raise_exception=True)
        datos = dict(serializador.validated_data)
        uuid_folio = datos.pop("uuid_folio")

        _, creado = BoletoPublicado.objects.update_or_create(
            uuid_folio=uuid_folio, defaults=datos
        )
        logger.info(
            "Boleto %s | folio=%s", "creado" if creado else "actualizado", uuid_folio
        )
        return Response(
            {"estado": "recibido", "creado": creado},
            status=status.HTTP_201_CREATED if creado else status.HTTP_200_OK,
        )

    @staticmethod
    def _token_valido(request, esperado: str) -> bool:
        cabecera = request.META.get("HTTP_AUTHORIZATION", "")
        prefijo = "Bearer "
        if not cabecera.startswith(prefijo):
            return False
        recibido = cabecera[len(prefijo) :].strip()
        return secrets.compare_digest(recibido, esperado)


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
    Localiza el boleto en el origen que tenga este despliegue.

    Se consulta primero el folio local y después la copia publicada, sin
    ninguna variable de configuración que lo decida. El motivo es que una
    bandera obligaría a acertar al desplegar, y equivocarse produciría un
    «boleto no disponible» con todo correctamente guardado —el peor fallo de
    diagnosticar—. Así, cada despliegue responde desde lo que realmente tiene:

    * Aplicación web completa: la caja vive aquí, hay folios, y el boleto se
      construye del folio enmascarando al presentar.
    * Servicio de solo lectura: no hay folios, y responde la copia publicada,
      que llegó ya enmascarada desde la caja.

    Las dos ramas no pueden coexistir para un mismo identificador: un servicio
    que tiene el folio es la caja y no se publica boletos a sí mismo.
    """
    from apps.estacionamiento.models import Folio

    folio = (
        Folio.objects.select_related("tarifa", "cajon")
        .filter(uuid=uuid_folio)
        .first()
    )
    if folio is not None:
        return desde_folio(folio)

    publicado = BoletoPublicado.objects.filter(uuid_folio=uuid_folio).first()
    return None if publicado is None else desde_publicado(publicado)


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

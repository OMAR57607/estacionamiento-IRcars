"""
Envío de boletos de la caja al servicio de consulta en la nube.

Reglas que gobiernan este módulo, en orden de importancia:

1. **La caja nunca espera a la red.** Ninguna función de aquí se invoca en el
   camino de una entrada o un cobro salvo `encolar`, que solo escribe una fila
   local. El envío ocurre en un hilo aparte. Si internet se cae, la caseta
   sigue cobrando y los boletos se publican cuando vuelva.

2. **El token jamás se registra en bitácora.** Va en una cabecera y no aparece
   en ningún mensaje de error, ni siquiera al diagnosticar un 401.

3. **El token jamás viaja en claro.** Publicar por HTTP sin cifrar expondría la
   credencial a cualquiera en la ruta, y con ella la capacidad de inyectar
   boletos falsos. Se exige HTTPS y solo se admite lo contrario con una
   autorización explícita, pensada para pruebas en la máquina local.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .instantanea import instantanea_publica
from .models import PublicacionPendiente

logger = logging.getLogger(__name__)

# Tiempo máximo de espera de una petición. Corto a propósito: este hilo no
# tiene prisa y un socket colgado durante minutos retrasaría toda la bandeja.
TIEMPO_LIMITE_SEGUNDOS = 10

# Espera entre reintentos: 15 s, 30 s, 60 s… con techo de 10 minutos. El techo
# importa porque un corte largo de internet no debe degenerar en una espera de
# horas una vez que la conexión regresa.
ESPERA_BASE_SEGUNDOS = 15
ESPERA_MAXIMA_SEGUNDOS = 600

# Un boleto sin publicar durante tres días ya no le sirve a nadie: el vehículo
# salió hace mucho. Se descarta para que una mala configuración no haga crecer
# la bandeja sin límite.
DIAS_PARA_DESCARTAR = 3

# Cadencia del hilo de envío en reposo.
INTERVALO_HILO_SEGUNDOS = 20

# Lote por ciclo. Acotado para que un respaldo grande no monopolice la
# escritura de SQLite, que la caja necesita para cobrar.
TAMANO_LOTE = 25


# --------------------------------------------------------------- Configuración


def publicacion_activa() -> bool:
    """
    Indica si esta instalación debe publicar boletos.

    Desactivada por omisión y de forma deliberada: una caja que se actualice
    sin haber configurado el servicio no debe empezar a emitir peticiones a
    ninguna parte por el solo hecho de instalar la versión nueva.
    """
    return bool(
        getattr(settings, "IRCARS_PUBLICAR_BOLETOS", False)
        and getattr(settings, "IRCARS_URL_BOLETOS", "")
        and getattr(settings, "IRCARS_TOKEN_PUBLICACION", "")
    )


def url_ingesta() -> str:
    base = str(getattr(settings, "IRCARS_URL_BOLETOS", "")).rstrip("/")
    return f"{base}/api/v1/boletos/"


class ErrorPublicacion(Exception):
    """Fallo de envío. `permanente` distingue un problema de configuración."""

    def __init__(self, mensaje: str, *, permanente: bool = False):
        super().__init__(mensaje)
        self.permanente = permanente


def _verificar_transporte(url: str) -> None:
    """Impide que la credencial de publicación viaje sin cifrar."""
    if url.startswith("https://"):
        return
    if getattr(settings, "IRCARS_PERMITIR_HTTP_BOLETOS", False):
        logger.warning(
            "Publicando boletos por HTTP sin cifrar. Admisible solo en pruebas "
            "locales: el token de publicación viaja expuesto."
        )
        return
    # Permanente: una dirección mal configurada no se arregla reintentando,
    # la arregla una persona. Clasificarlo como fallo transitorio produciría
    # una petición cada quince segundos durante días.
    raise ErrorPublicacion(
        "IRCARS_URL_BOLETOS debe usar https://. Publicar por HTTP expondría el "
        "token y permitiría inyectar boletos falsos. Para pruebas locales, "
        "active IRCARS_PERMITIR_HTTP_BOLETOS.",
        permanente=True,
    )


# ------------------------------------------------------------------- Encolado


def encolar(folio) -> None:
    """
    Registra —o actualiza— la intención de publicar este folio.

    Se invoca desde una señal `post_save`, es decir, dentro de la misma
    transacción que grabó el folio. Eso es justamente lo que se busca: si la
    transacción se revierte, la fila de la bandeja desaparece con ella y no
    queda el aviso de un folio que nunca existió.

    Un fallo aquí **no debe tumbar la operación**. Registrar una entrada es la
    función esencial del sistema; publicar el boleto es una comodidad. Si lo
    segundo falla, se anota en bitácora y la caja continúa.
    """
    if not publicacion_activa():
        return

    try:
        cuerpo = instantanea_publica(folio)
        # Punto de guardado propio. Es la pieza que hace segura la captura de
        # abajo: sin él, un error de base de datos aquí dejaría la transacción
        # del folio marcada para revertirse, y la entrada fallaría al
        # confirmarse **aunque la excepción estuviera capturada**. Con el
        # punto de guardado, lo único que se revierte es el encolado.
        with transaction.atomic():
            PublicacionPendiente.objects.update_or_create(
                uuid_folio=folio.uuid,
                defaults={
                    "cuerpo": cuerpo,
                    # Un cambio de estado reinicia la cuenta de reintentos: lo
                    # que falló al enviar era la versión anterior, y la nueva
                    # merece su propia oportunidad inmediata.
                    "intentos": 0,
                    "proximo_intento_en": timezone.now(),
                    "ultimo_error": "",
                },
            )
    except Exception:
        logger.exception(
            "No se pudo encolar la publicación del folio %s. La operación de "
            "caja no se interrumpe.",
            getattr(folio, "uuid", "?"),
        )


# -------------------------------------------------------------------- Entrega


def _enviar(cuerpo: dict) -> None:
    """
    Entrega una instantánea al servicio de boletos.

    La ingesta es idempotente en el servidor, de modo que reenviar una carga ya
    recibida es inofensivo. Esa propiedad es la que permite reintentar sin
    llevar la cuenta de qué se entregó: ante la duda, se reenvía.
    """
    url = url_ingesta()
    _verificar_transporte(url)

    peticion = urllib.request.Request(
        url,
        data=json.dumps(cuerpo).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.IRCARS_TOKEN_PUBLICACION}",
            "User-Agent": "IRcars-Caja/1.0",
        },
    )

    try:
        with urllib.request.urlopen(peticion, timeout=TIEMPO_LIMITE_SEGUNDOS):
            return
    except urllib.error.HTTPError as exc:
        # 408 y 429 son transitorios por definición; el resto de los 4xx
        # señala un contrato roto o un token inválido, y reintentar cada
        # quince segundos no lo arreglará: lo arregla una persona.
        permanente = 400 <= exc.code < 500 and exc.code not in (408, 429)
        raise ErrorPublicacion(
            f"El servicio respondió HTTP {exc.code}.", permanente=permanente
        ) from exc
    except urllib.error.URLError as exc:
        raise ErrorPublicacion(f"No se pudo contactar al servicio: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise ErrorPublicacion(f"Fallo de red: {exc}") from exc


def _reprogramar(pendiente: PublicacionPendiente, error: ErrorPublicacion) -> None:
    """Aplica la espera exponencial y deja constancia del motivo."""
    pendiente.intentos += 1

    if error.permanente:
        # Espera larga y aviso enfático: esto no se resuelve reintentando.
        espera = ESPERA_MAXIMA_SEGUNDOS
        logger.error(
            "Publicación rechazada por el servicio (folio %s): %s. Revise "
            "IRCARS_TOKEN_PUBLICACION y la versión del contrato.",
            pendiente.uuid_folio,
            error,
        )
    else:
        espera = min(
            ESPERA_BASE_SEGUNDOS * (2 ** (pendiente.intentos - 1)),
            ESPERA_MAXIMA_SEGUNDOS,
        )
        logger.warning(
            "Reintento %s de la publicación del folio %s en %s s: %s",
            pendiente.intentos,
            pendiente.uuid_folio,
            espera,
            error,
        )

    pendiente.ultimo_error = str(error)[:500]
    pendiente.proximo_intento_en = timezone.now() + timedelta(seconds=espera)
    pendiente.save(
        update_fields=["intentos", "ultimo_error", "proximo_intento_en", "actualizada_en"]
    )


def descartar_vencidas() -> int:
    """Elimina lo que ya no tiene sentido publicar. Devuelve cuántas borró."""
    limite = timezone.now() - timedelta(days=DIAS_PARA_DESCARTAR)
    borradas, _ = PublicacionPendiente.objects.filter(creada_en__lt=limite).delete()
    if borradas:
        logger.warning(
            "Se descartaron %s publicaciones con más de %s días sin entregar.",
            borradas,
            DIAS_PARA_DESCARTAR,
        )
    return borradas


def drenar(limite: int = TAMANO_LOTE) -> tuple[int, int]:
    """
    Envía lo pendiente que ya cumplió su espera. Devuelve (enviadas, fallidas).

    Cada fila se procesa de forma independiente: el fallo de una no detiene a
    las demás, porque un solo folio con una carga defectuosa no debe impedir
    que se publique el resto del día.
    """
    if not publicacion_activa():
        return (0, 0)

    descartar_vencidas()

    pendientes = list(
        PublicacionPendiente.objects.filter(
            proximo_intento_en__lte=timezone.now()
        ).order_by("proximo_intento_en")[:limite]
    )

    enviadas = fallidas = 0
    for pendiente in pendientes:
        try:
            _enviar(pendiente.cuerpo)
        except ErrorPublicacion as exc:
            _reprogramar(pendiente, exc)
            fallidas += 1
        except Exception as exc:  # defensa: un fallo inesperado no detiene el lote
            _reprogramar(pendiente, ErrorPublicacion(f"Error inesperado: {exc}"))
            fallidas += 1
        else:
            # Entregado: la fila cumplió su función y desaparece. La bandeja
            # es una cola de trabajo, no un historial; el historial de folios
            # ya existe y es el autoritativo.
            pendiente.delete()
            enviadas += 1
            logger.info("Boleto publicado | folio=%s", pendiente.uuid_folio)

    return (enviadas, fallidas)


# ---------------------------------------------------------------- Hilo de envío

_detener = threading.Event()
_hilo: threading.Thread | None = None


def _ciclo() -> None:
    while not _detener.is_set():
        try:
            # Django abre una conexión por hilo. Cerrarla al terminar cada
            # ciclo evita acumular descriptores y, en SQLite, prolongar
            # bloqueos de escritura que la caja necesita libres.
            close_old_connections()
            drenar()
        except Exception:
            logger.exception("Fallo del ciclo de publicación. El hilo continúa.")
        finally:
            close_old_connections()
        _detener.wait(INTERVALO_HILO_SEGUNDOS)


def iniciar_hilo() -> bool:
    """
    Arranca el envío en segundo plano. Idempotente. Devuelve si quedó activo.

    Hilo demonio: si la ventana se cierra, el proceso termina sin esperarlo. Lo
    que quede sin publicar sigue en la bandeja y se enviará en el próximo
    arranque, que es exactamente el comportamiento deseado.
    """
    global _hilo

    if not publicacion_activa():
        logger.info("Publicación de boletos desactivada en esta instalación.")
        return False
    if _hilo is not None and _hilo.is_alive():
        return True

    _detener.clear()
    _hilo = threading.Thread(
        target=_ciclo, name="ircars-publicador-boletos", daemon=True
    )
    _hilo.start()
    logger.info("Hilo de publicación de boletos iniciado.")
    return True


def detener_hilo(espera_segundos: float = 5.0) -> None:
    """Solicita la parada ordenada del hilo. Útil en pruebas y al cerrar."""
    _detener.set()
    if _hilo is not None and _hilo.is_alive():
        _hilo.join(timeout=espera_segundos)


def publicar_ahora() -> None:
    """
    Intenta publicar de inmediato, sin esperar el siguiente ciclo del hilo.

    Se invoca al mostrar el código QR al cliente. Sin esto, el boleto tardaría
    hasta `INTERVALO_HILO_SEGUNDOS` en estar disponible, y el cliente que
    escanea en el acto vería una página de «boleto no disponible» —el peor
    primer contacto posible con la función—.

    No bloquea: la ventana de la caja debe seguir respondiendo. Si falla, lo
    pendiente queda en la bandeja y el hilo lo reintentará.
    """
    if not publicacion_activa():
        return

    def _intento() -> None:
        try:
            close_old_connections()
            drenar(limite=5)
        except Exception:
            logger.exception("Fallo la publicación inmediata. Quedará en la bandeja.")
        finally:
            close_old_connections()

    threading.Thread(
        target=_intento, name="ircars-publicacion-inmediata", daemon=True
    ).start()

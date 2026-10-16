"""
Arranque del ejecutable local.

Inicializa Django como biblioteca —sin servidor— y deja la base de datos lista
antes de que la interfaz aparezca. El orden es importante: ningún modelo puede
importarse antes de `django.setup()`, por lo que este módulo no debe importar
nada de `apps.estacionamiento` en su nivel superior.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_inicializado = False


def iniciar(aplicar_migraciones: bool = True) -> None:
    """
    Prepara Django y la base de datos. Idempotente.

    Las migraciones se aplican en cada arranque: es lo correcto para una
    aplicación de escritorio que se actualiza reemplazando el ejecutable. El
    operador no debe ejecutar comandos para que una versión nueva funcione con
    sus datos existentes.
    """
    global _inicializado
    if _inicializado:
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_local")

    import django

    django.setup()

    if aplicar_migraciones:
        from django.core.management import call_command

        try:
            call_command("migrate", "--noinput", verbosity=0)
        except Exception:
            # Si el esquema no puede prepararse, la aplicación no debe abrir una
            # ventana que luego falle en cada operación: se registra y se
            # propaga para que el punto de entrada lo informe con claridad.
            logger.exception("Falló la preparación de la base de datos")
            raise

    _iniciar_publicacion_de_boletos()

    _inicializado = True
    logger.info("Aplicación iniciada | python=%s", sys.version.split()[0])


def _iniciar_publicacion_de_boletos() -> None:
    """
    Arranca el envío de boletos al servicio de consulta, si está configurado.

    Va después de las migraciones porque el hilo lee la bandeja de salida, y
    esa tabla podría no existir todavía en un equipo que acaba de actualizarse.

    Cualquier fallo aquí se registra y se ignora. El criterio es el mismo que
    rige todo este subsistema: **la caja cobra con o sin boletos en la nube.**
    Que no se pueda publicar no es razón para que el programa no abra.
    """
    try:
        from apps.boletos.publicador import iniciar_hilo

        iniciar_hilo()
    except Exception:
        logger.exception(
            "No se pudo iniciar la publicación de boletos. La caja opera con "
            "normalidad; los boletos quedarán en la bandeja de salida."
        )


def ruta_base_datos() -> str:
    """Ubicación del archivo de datos, para mostrarla al usuario."""
    from django.conf import settings

    return settings.DATABASES["default"]["NAME"]


def ruta_bitacora() -> str:
    """Ubicación de la bitácora, para mostrarla al usuario."""
    from django.conf import settings

    return str(settings.RUTA_BITACORA)

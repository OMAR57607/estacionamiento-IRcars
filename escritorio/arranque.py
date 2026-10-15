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


def _activar_wal() -> None:
    """
    Activa el modo WAL de SQLite en cada conexión nueva.

    Dos razones concretas:

    * **Resistencia a cortes de energía.** WAL mantiene un registro de
      escritura anticipada; ante un apagón —cosa habitual en un
      estacionamiento— la base se recupera de forma consistente en lugar de
      quedar corrupta. En una caja esto equivale a no perder los cobros del
      turno.
    * **Lecturas sin bloqueo.** El tablero de vehículos dentro puede
      refrescarse mientras se registra un cobro, sin esperar.

    `synchronous=NORMAL` es el equilibrio adecuado con WAL: conserva la
    integridad ante caída del proceso y evita el costo de sincronizar el disco
    en cada transacción.
    """
    from django.db.backends.signals import connection_created

    def configurar(sender, connection, **kwargs):
        if connection.vendor != "sqlite":
            return
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            # Integridad referencial activa: SQLite la desactiva por omisión.
            cursor.execute("PRAGMA foreign_keys=ON;")

    # weak=False es indispensable: las señales de Django guardan referencias
    # débiles, y `configurar` es una función local. Sin esto el recolector de
    # basura la elimina antes del primer uso y el manejador nunca se ejecuta:
    # la base quedaría en modo `delete`, sin la protección de WAL, y el fallo
    # sería silencioso —se descubriría con un apagón y una base corrupta—.
    connection_created.connect(configurar, weak=False)


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
    _activar_wal()

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

    _inicializado = True
    logger.info("Aplicación iniciada | python=%s", sys.version.split()[0])


def ruta_base_datos() -> str:
    """Ubicación del archivo de datos, para mostrarla al usuario."""
    from django.conf import settings

    return settings.DATABASES["default"]["NAME"]


def ruta_bitacora() -> str:
    """Ubicación de la bitácora, para mostrarla al usuario."""
    from django.conf import settings

    return str(settings.RUTA_BITACORA)

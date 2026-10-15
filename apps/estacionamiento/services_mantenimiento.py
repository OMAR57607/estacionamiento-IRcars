"""
Capa de aplicación: mantenimiento de los datos.

Existe por una necesidad concreta y acotada: tras las pruebas de instalación,
la base queda con boletos, turnos y precios que no corresponden a ninguna
operación real. Dejarlos ahí envenena el primer corte de caja y cualquier
reporte posterior, porque nadie distingue después un cobro de prueba de uno de
verdad.

Tres decisiones gobiernan este módulo:

* **Se respalda antes de borrar.** La copia se hace con la API de respaldo de
  SQLite y no copiando el archivo: en modo WAL hay escrituras que todavía
  viven en el diario, de modo que una copia a lo bruto puede salir incompleta
  justo cuando más falta hace. El respaldo convierte una acción irreversible
  en una reversible, que es lo que se le debe exigir a un botón que borra.

* **Se borra todo o no se borra nada.** Los folios referencian turnos y
  tarifas con `PROTECT`, así que el orden importa y un borrado a medias
  dejaría la base en un estado que la aplicación no sabe interpretar.

* **Nunca se tocan los operadores.** Son la identidad con la que se firma cada
  movimiento y la única pieza cuya pérdida obliga a reinstalar. El precio
  vigente tampoco se toca: sin él la caseta no puede ni abrir.

Está pensado para retirarse cuando la operación madure —el dueño lo usa
mientras atiende él mismo la caseta—, y por eso vive en su propio archivo en
lugar de repartido entre los servicios existentes.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .domain.exceptions import ErrorConfiguracion
from .models import EstadoFolio, EstadoTurno, Folio, Tarifa, Turno

logger = logging.getLogger(__name__)


def resumen_movimientos() -> dict:
    """
    Qué hay ahora mismo en la base, para enseñarlo antes de borrar nada.

    Un diálogo de borrado que no dice cuántas cosas se va a llevar obliga a
    confiar a ciegas; con las cifras delante, un número inesperado detiene al
    usuario a tiempo.
    """
    return {
        "folios": Folio.objects.count(),
        "folios_abiertos": Folio.objects.filter(estado=EstadoFolio.ABIERTO).count(),
        "turnos": Turno.objects.count(),
        "turnos_abiertos": Turno.objects.filter(estado=EstadoTurno.ABIERTO).count(),
        "precios": Tarifa.objects.count(),
        "precios_historicos": Tarifa.objects.filter(activa=False).count(),
    }


def respaldar_base(destino: Path | None = None) -> Path | None:
    """
    Copia consistente de la base, junto al archivo original.

    Devuelve la ruta del respaldo, o `None` si el motor no es SQLite —en el
    servidor el respaldo es responsabilidad de la infraestructura, no de una
    pantalla de caja—.

    Se usa `Connection.backup` y no `shutil.copy` a propósito: con el diario
    WAL activo, parte de lo escrito recientemente está fuera del archivo
    principal, y una copia plana lo perdería sin avisar.
    """
    if connection.vendor != "sqlite":
        return None

    # La API de respaldo de SQLite reintenta en bucle mientras la base de
    # origen esté bloqueada, y una transacción abierta la bloquea: dentro de
    # un `atomic` esto no fallaría, se quedaría colgado para siempre. Más vale
    # un error que lo diga que una aplicación congelada sin explicación.
    if connection.in_atomic_block:
        raise ErrorConfiguracion(
            "El respaldo no puede hacerse dentro de una transacción abierta. "
            "Debe ejecutarse antes de empezar a borrar."
        )

    if destino is None:
        origen = Path(settings.DATABASES["default"]["NAME"])
        marca = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        destino = origen.parent / f"respaldo-antes-de-borrar-{marca}.sqlite3"

    connection.ensure_connection()
    copia = sqlite3.connect(str(destino))
    try:
        connection.connection.backup(copia)
    finally:
        copia.close()

    logger.warning("Respaldo creado antes de borrar | archivo=%s", destino)
    return destino


def purgar_movimientos(
    *,
    operador,
    incluir_historico_precios: bool = False,
    respaldar: bool = True,
) -> dict:
    """
    Borra boletos y turnos, conservando operadores y el precio vigente.

    `incluir_historico_precios` limpia además los precios ya desactivados, que
    después de unas pruebas suelen ser media docena de cifras que nunca
    estuvieron exhibidas. El precio en uso jamás se borra: es la condición sin
    la cual la caseta no puede operar.

    Devuelve lo que se llevó por delante y dónde quedó el respaldo, para
    poder decírselo al usuario y dejarlo en la bitácora.
    """
    if operador is None or not getattr(operador, "pk", None):
        raise ErrorConfiguracion(
            "El borrado requiere un operador identificado: queda registrado a "
            "nombre de quien lo ejecuta."
        )

    # El respaldo va fuera de la transacción y antes de tocar nada: dentro de
    # ella copiaría un estado que todavía puede deshacerse.
    respaldo = respaldar_base() if respaldar else None

    with transaction.atomic():
        # El orden lo imponen las claves foráneas `PROTECT`: los folios
        # referencian turnos y tarifas, así que se van primero.
        folios, _ = Folio.objects.all().delete()
        turnos, _ = Turno.objects.all().delete()

        precios = 0
        if incluir_historico_precios:
            precios, _ = Tarifa.objects.filter(activa=False).delete()

    resultado = {
        "folios_borrados": folios,
        "turnos_borrados": turnos,
        "precios_borrados": precios,
        "respaldo": str(respaldo) if respaldo else None,
    }

    # Nivel WARNING a propósito: es la operación más destructiva de la
    # aplicación y debe saltar a la vista al revisar la bitácora, sin que
    # nadie tenga que buscarla.
    logger.warning(
        "Movimientos borrados | operador=%s folios=%s turnos=%s precios=%s respaldo=%s",
        getattr(operador, "username", operador),
        resultado["folios_borrados"],
        resultado["turnos_borrados"],
        resultado["precios_borrados"],
        resultado["respaldo"],
    )
    return resultado

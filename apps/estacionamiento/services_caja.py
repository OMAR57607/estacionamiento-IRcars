"""
Capa de aplicación: control de caja y corte de turno.

Se separa de `services.py` por cohesión: ahí vive la operación del vehículo
(entrada, cobro, salida), aquí el control del efectivo. Son responsabilidades
distintas, con reglas distintas y auditorías distintas.

El cálculo de la diferencia no está aquí, sino en `domain/corte.py`, que es
lógica pura. Este módulo solo orquesta: bloquea, consulta y persiste.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .domain.corte import ResumenCorte, calcular_corte
from .domain.exceptions import (
    ErrorConfiguracion,
    TurnoConFoliosAbiertos,
    TurnoYaAbierto,
    TurnoYaCerrado,
)
from .models import EstadoFolio, EstadoTurno, Folio, Turno

logger = logging.getLogger(__name__)


@transaction.atomic
def abrir_turno(
    *,
    operador,
    fondo_inicial: Decimal = Decimal("0.00"),
    momento: datetime | None = None,
    observaciones: str = "",
) -> Turno:
    """
    Caso de uso: apertura de caja.

    El operador declara el fondo inicial —el efectivo de cambio con el que
    arranca—. Sin ese dato el corte no tiene línea base y cualquier diferencia
    sería ininterpretable.
    """
    momento = momento or timezone.now()

    if operador is None or not getattr(operador, "pk", None):
        raise ErrorConfiguracion(
            "La apertura de turno requiere un operador identificado: el corte "
            "se atribuye a una persona, no al sistema."
        )
    if fondo_inicial < 0:
        raise ErrorConfiguracion("El fondo inicial no puede ser negativo.")

    if Turno.objects.filter(operador=operador, estado=EstadoTurno.ABIERTO).exists():
        raise TurnoYaAbierto(
            "Este operador ya tiene un turno abierto. Ciérrelo antes de abrir "
            "uno nuevo."
        )

    turno = Turno.objects.create(
        operador=operador,
        fondo_inicial=fondo_inicial,
        abierto_en=momento,
        estado=EstadoTurno.ABIERTO,
        observaciones=observaciones,
    )
    logger.info(
        "Turno abierto | turno=%s operador=%s fondo=%s",
        turno.pk,
        getattr(operador, "username", operador),
        fondo_inicial,
    )
    return turno


def _importes_del_turno(turno: Turno) -> list[Decimal]:
    """Importes de los folios cobrados durante el turno, en orden de cierre."""
    return list(
        Folio.objects.filter(turno=turno, estado=EstadoFolio.CERRADO)
        .order_by("salida_en")
        .values_list("importe", flat=True)
    )


def resumen_operativo(turno: Turno) -> dict:
    """
    Avance del turno **sin revelar importes**.

    Es la vista que el operador puede consultar mientras su turno está abierto.
    Informa cuántos folios ha cobrado y cuántos siguen dentro, pero no el total
    de efectivo esperado: revelarlo anularía el cierre ciego, porque el conteo
    físico se ajustaría a esa cifra en lugar de ser independiente.
    """
    agregados = Folio.objects.filter(turno=turno).aggregate(
        cobrados=Count("id", filter=Q(estado=EstadoFolio.CERRADO)),
        cortesias=Count(
            "id", filter=Q(estado=EstadoFolio.CERRADO, importe=Decimal("0.00"))
        ),
    )
    return {
        "turno": turno.pk,
        "operador": getattr(turno.operador, "username", str(turno.operador)),
        "abierto_en": turno.abierto_en,
        "estado": turno.estado,
        "folios_cobrados": agregados["cobrados"] or 0,
        "folios_cortesia": agregados["cortesias"] or 0,
        "folios_abiertos_en_patio": Folio.objects.filter(
            estado=EstadoFolio.ABIERTO
        ).count(),
    }


def calcular_resumen(turno: Turno, efectivo_declarado: Decimal | None = None) -> ResumenCorte:
    """
    Corte completo del turno, con importes.

    Uso restringido: mientras el turno está abierto, esta información no debe
    mostrarse al propio operador antes de su conteo. La interfaz de caja la
    invoca únicamente **después** de recibir el efectivo declarado; el perfil
    administrativo puede consultarla en cualquier momento.
    """
    if turno.estado == EstadoTurno.CERRADO and efectivo_declarado is None:
        # Un turno ya cortado devuelve su fotografía, no un recálculo: la cifra
        # declarada por el operador es inmutable.
        return ResumenCorte(
            fondo_inicial=turno.fondo_inicial,
            total_cobrado=turno.total_cobrado or Decimal("0.00"),
            efectivo_esperado=turno.efectivo_esperado or turno.fondo_inicial,
            folios_cobrados=turno.folios_cobrados or 0,
            folios_cortesia=turno.folios_cortesia or 0,
            efectivo_declarado=turno.efectivo_declarado,
            diferencia=turno.diferencia,
        )

    return calcular_corte(
        fondo_inicial=turno.fondo_inicial,
        importes_cobrados=_importes_del_turno(turno),
        efectivo_declarado=efectivo_declarado,
    )


@transaction.atomic
def cerrar_turno(
    *,
    turno_id: int,
    efectivo_declarado: Decimal,
    momento: datetime | None = None,
    observaciones: str = "",
    permitir_folios_abiertos: bool = True,
) -> tuple[Turno, ResumenCorte]:
    """
    Caso de uso: cierre ciego y corte de caja.

    El operador entrega la cifra que contó físicamente; solo entonces se
    calcula y revela la diferencia. El resultado se graba como fotografía
    inmutable en el turno.

    `permitir_folios_abiertos` se deja en verdadero por omisión porque es la
    realidad operativa: al terminar un turno siempre quedan vehículos dentro,
    y sus folios se cobrarán en el siguiente. Ponerlo en falso fuerza a vaciar
    el patio antes de cortar, lo cual solo aplica a un cierre de jornada.
    """
    momento = momento or timezone.now()

    if efectivo_declarado is None:
        raise ErrorConfiguracion(
            "El cierre requiere el efectivo contado. Un corte sin conteo "
            "físico no verifica nada."
        )
    if efectivo_declarado < 0:
        raise ErrorConfiguracion("El efectivo declarado no puede ser negativo.")

    turno = Turno.objects.select_for_update().filter(pk=turno_id).first()
    if turno is None:
        raise ErrorConfiguracion(f"No existe el turno {turno_id}.")
    if turno.estado != EstadoTurno.ABIERTO:
        raise TurnoYaCerrado(
            f"El turno {turno.pk} ya fue cortado el "
            f"{turno.cerrado_en:%Y-%m-%d %H:%M}."
        )

    if not permitir_folios_abiertos:
        pendientes = Folio.objects.filter(estado=EstadoFolio.ABIERTO).count()
        if pendientes:
            raise TurnoConFoliosAbiertos(
                f"Quedan {pendientes} folios abiertos en el patio. Su cobro "
                "caería en el turno siguiente y distorsionaría ambos cortes."
            )

    resumen = calcular_corte(
        fondo_inicial=turno.fondo_inicial,
        importes_cobrados=_importes_del_turno(turno),
        efectivo_declarado=efectivo_declarado,
    )

    turno.estado = EstadoTurno.CERRADO
    turno.cerrado_en = momento
    turno.efectivo_declarado = resumen.efectivo_declarado
    turno.total_cobrado = resumen.total_cobrado
    turno.efectivo_esperado = resumen.efectivo_esperado
    turno.diferencia = resumen.diferencia
    turno.folios_cobrados = resumen.folios_cobrados
    turno.folios_cortesia = resumen.folios_cortesia
    turno.resumen = resumen.a_dict()
    if observaciones:
        turno.observaciones = f"{turno.observaciones}\n{observaciones}".strip()
    turno.save()

    # El faltante o sobrante se registra en nivel WARNING: debe destacar en la
    # bitácora sin que nadie tenga que buscarlo.
    nivel = logger.info if resumen.cuadra else logger.warning
    nivel(
        "Turno cerrado | turno=%s operador=%s esperado=%s declarado=%s diferencia=%s",
        turno.pk,
        getattr(turno.operador, "username", turno.operador),
        resumen.efectivo_esperado,
        resumen.efectivo_declarado,
        resumen.diferencia,
    )
    return turno, resumen


def reporte_por_rango(desde: datetime, hasta: datetime) -> dict:
    """
    Reporte administrativo de un periodo, con independencia de los turnos.

    Responde a la pregunta del dueño: cuánto se cobró, cuántos vehículos
    entraron y cuánta cortesía se otorgó entre dos fechas. Se resuelve con
    consultas agregadas en la base, no recorriendo folios en memoria.
    """
    if hasta < desde:
        raise ErrorConfiguracion("El rango de fechas está invertido.")

    cobrados = Folio.objects.filter(
        estado=EstadoFolio.CERRADO, salida_en__gte=desde, salida_en__lte=hasta
    )
    agregados = cobrados.aggregate(
        total=Sum("importe"),
        minutos=Sum("minutos"),
        folios=Count("id"),
        cortesias=Count("id", filter=Q(importe=Decimal("0.00"))),
    )
    folios = agregados["folios"] or 0
    total = agregados["total"] or Decimal("0.00")
    minutos = agregados["minutos"] or 0

    return {
        "desde": desde,
        "hasta": hasta,
        "folios_cobrados": folios,
        "folios_cortesia": agregados["cortesias"] or 0,
        "total_cobrado": total,
        "minutos_totales": minutos,
        # Promedios útiles para detectar anomalías de tarificación o de captura.
        "importe_promedio": (total / folios).quantize(Decimal("0.01")) if folios else Decimal("0.00"),
        "minutos_promedio": round(minutos / folios, 1) if folios else 0.0,
        "turnos_cerrados": Turno.objects.filter(
            estado=EstadoTurno.CERRADO, cerrado_en__gte=desde, cerrado_en__lte=hasta
        ).count(),
    }

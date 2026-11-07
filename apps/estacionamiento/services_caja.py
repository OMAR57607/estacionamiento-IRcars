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

from .domain.corte import (
    ResultadoAjuste,
    ResumenCorte,
    calcular_ajuste,
    calcular_corte,
    justificacion_suficiente,
)
from .domain.exceptions import (
    AjusteNoAplicable,
    ErrorConfiguracion,
    TurnoConFoliosAbiertos,
    TurnoYaAbierto,
    TurnoYaCerrado,
)
from .models import AjusteCorte, EstadoFolio, EstadoTurno, Folio, MotivoAjuste, Turno

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


# --- Corrección de un corte ya firmado --------------------------------------


def veredicto_del_corte(turno: Turno) -> dict:
    """
    Qué debe leerse hoy sobre este corte: cuadra, falta o sobra.

    Existe para que la regla «si hay ajuste, la cifra válida es la ajustada»
    se escriba una sola vez. Estaba a punto de repetirse en la pantalla del
    corte, en el reporte y en el PDF, y basta con olvidarla en uno de los tres
    para que un documento que alguien firma declare un faltante ya corregido.

    Devuelve las dos cifras, no solo la vigente: el comprobante debe poder
    mostrar de dónde se partió. Un ajuste que oculta la diferencia original
    sería un borrado con otro nombre.
    """
    vigente = turno.diferencia_vigente
    return {
        "diferencia": vigente,
        "diferencia_original": turno.diferencia,
        "efectivo_vigente": turno.efectivo_vigente,
        "ajustado": turno.tiene_ajuste,
        "cuadra": vigente is not None and vigente == Decimal("0.00"),
        "hay_faltante": vigente is not None and vigente < 0,
        "hay_sobrante": vigente is not None and vigente > 0,
    }


@transaction.atomic
def ajustar_corte(
    *,
    turno_id: int,
    efectivo_corregido: Decimal,
    motivo: str,
    justificacion: str,
    autorizo,
    momento: datetime | None = None,
) -> tuple[Turno, AjusteCorte, ResultadoAjuste]:
    """
    Caso de uso: corregir el efectivo declarado de un corte ya cerrado.

    Nace de un cierre equivocado en producción —un turno cortado con $140
    menos de los que había en caja— que el sistema no permitía enmendar. El
    operador no se puede desdecir y el administrador no tenía más opción que
    convivir con un faltante inexistente en el histórico.

    Lo que hace y lo que deliberadamente **no** hace:

    * No reabre el turno. Reabrir obligaría a repetir el cierre ciego, y el
      cierre ciego ya cumplió su función: el operador contó sin conocer el
      esperado. Repetirlo ahora, con la cifra esperada a la vista, no
      verificaría nada.
    * No reescribe `efectivo_declarado` ni `diferencia`. La cifra que el
      operador declaró es un hecho, y los hechos no se editan. Queda en el
      turno tal como se firmó.
    * Sí registra un asiento con la cifra correcta, el motivo, la
      justificación escrita y quién respondió por ella, y actualiza los campos
      derivados para que los reportes lean la realidad sin recorrer la
      bitácora.

    Se parte del efectivo **vigente** —el del último ajuste si ya hubo uno— y
    no del original, para que dos correcciones sucesivas se acumulen en lugar
    de pisarse.
    """
    momento = momento or timezone.now()

    if autorizo is None or not getattr(autorizo, "pk", None):
        raise ErrorConfiguracion(
            "El ajuste de un corte requiere un administrador identificado: "
            "una corrección de caja sin responsable no es auditable."
        )
    if efectivo_corregido is None:
        raise ErrorConfiguracion("Capture el efectivo real que había en caja.")
    if efectivo_corregido < 0:
        raise ErrorConfiguracion("El efectivo real no puede ser negativo.")
    if motivo not in MotivoAjuste.values:
        raise ErrorConfiguracion(f"Motivo de ajuste desconocido: {motivo!r}.")
    if not justificacion_suficiente(justificacion):
        raise AjusteNoAplicable(
            "Explique qué pasó. Una justificación de una palabra no permite "
            "auditar la corrección meses después."
        )

    turno = Turno.objects.select_for_update().filter(pk=turno_id).first()
    if turno is None:
        raise ErrorConfiguracion(f"No existe el turno {turno_id}.")
    if turno.estado != EstadoTurno.CERRADO:
        raise AjusteNoAplicable(
            f"El turno {turno.pk} todavía está abierto: no hay corte que "
            "corregir. Ciérrelo con la cifra correcta."
        )

    # El esperado es la cifra que el sistema calculó al cerrar. Se toma de la
    # fotografía y no se recalcula: recalcularlo ahora, con folios que pueden
    # haberse cobrado después, cambiaría la base contra la que el operador
    # contó y el ajuste dejaría de ser comparable con el cierre.
    esperado = turno.efectivo_esperado
    if esperado is None:
        raise AjusteNoAplicable(
            f"El turno {turno.pk} no tiene corte grabado y no puede ajustarse."
        )

    resultado = calcular_ajuste(
        efectivo_esperado=esperado,
        efectivo_declarado_vigente=turno.efectivo_vigente,
        efectivo_corregido=efectivo_corregido,
    )
    if resultado.sin_efecto:
        raise AjusteNoAplicable(
            "El efectivo capturado es idéntico al que ya tiene el corte: no "
            "hay nada que corregir."
        )

    ajuste = AjusteCorte.objects.create(
        turno=turno,
        efectivo_esperado=resultado.efectivo_esperado,
        efectivo_anterior=resultado.efectivo_anterior,
        efectivo_nuevo=resultado.efectivo_nuevo,
        monto=resultado.monto,
        diferencia_anterior=resultado.diferencia_anterior,
        diferencia_nueva=resultado.diferencia_nueva,
        motivo=motivo,
        justificacion=justificacion.strip(),
        autorizo=autorizo,
        creado_en=momento,
    )

    turno.efectivo_ajustado = resultado.efectivo_nuevo
    turno.diferencia_ajustada = resultado.diferencia_nueva
    # El resumen del corte lleva anexo el rastro de los ajustes. Así el JSON
    # que sustenta el comprobante sigue bastando por sí solo para reconstruir
    # el documento, sin consultar otra tabla.
    resumen = dict(turno.resumen or {})
    resumen.setdefault("ajustes", []).append(
        {
            **resultado.a_dict(),
            "motivo": motivo,
            "justificacion": justificacion.strip(),
            "autorizo": getattr(autorizo, "username", str(autorizo)),
            "registrado_en": momento.isoformat(),
        }
    )
    resumen["diferencia_vigente"] = str(resultado.diferencia_nueva)
    turno.resumen = resumen
    turno.save(
        update_fields=["efectivo_ajustado", "diferencia_ajustada", "resumen"]
    )

    # WARNING siempre, incluso cuando el ajuste deja la caja cuadrada. Que un
    # corte firmado se corrija es excepcional por definición, y la bitácora
    # debe permitir contar cuántas veces pasa y a quién: un patrón de ajustes
    # que siempre cuadran la caja es exactamente lo que se vería si alguien
    # usara esta función para tapar faltantes.
    logger.warning(
        "Corte ajustado | turno=%s autorizo=%s esperado=%s antes=%s ahora=%s "
        "monto=%s diferencia=%s->%s motivo=%s",
        turno.pk,
        getattr(autorizo, "username", autorizo),
        resultado.efectivo_esperado,
        resultado.efectivo_anterior,
        resultado.efectivo_nuevo,
        resultado.monto,
        resultado.diferencia_anterior,
        resultado.diferencia_nueva,
        motivo,
    )
    return turno, ajuste, resultado


def ajustes_del_turno(turno: Turno):
    """Bitácora de correcciones del turno, de la más antigua a la más reciente."""
    return (
        AjusteCorte.objects.select_related("autorizo")
        .filter(turno=turno)
        .order_by("creado_en")
    )


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


# --- Vigilancia del turno abierto -------------------------------------------
#
# El sistema avisaba en rojo cuando **no** había turno abierto —porque eso
# impide cobrar— y callaba cuando había uno abierto desde hacía catorce horas.
# Avisaba de lo que estorba y no de lo que hace daño en silencio: un turno que
# cruza la noche mezcla el efectivo de dos días y la diferencia del corte deja
# de ser interpretable, que es la única herramienta de control sobre la caja.

# A partir de cuántas horas un turno abierto deja de ser normal. Una jornada
# larga de caseta cabe de sobra; doce horas significan casi siempre que nadie
# cerró.
HORAS_TURNO_LARGO = 12


def estado_del_turno(turno: Turno, ahora: datetime | None = None) -> dict:
    """
    Cuánto lleva abierto un turno y si ya debería haberse cerrado.

    Devuelve un diccionario y no un booleano porque la pantalla necesita las
    dos cosas: decidir si avisa y decir cuánto lleva. El cálculo vive aquí, y
    no en la plantilla, porque de él depende que se pueda cobrar o no.

    `de_otro_dia` se mide sobre la fecha **local**, no sobre horas
    transcurridas: un turno abierto a las 23:50 y revisado a las 00:10 lleva
    veinte minutos, pero ya pertenece a otra jornada de caja y su corte
    mezclaría el efectivo de dos días.
    """
    ahora = ahora or timezone.now()
    transcurrido = ahora - turno.abierto_en
    horas = transcurrido.total_seconds() / 3600

    abierto_local = timezone.localtime(turno.abierto_en).date()
    hoy_local = timezone.localtime(ahora).date()
    de_otro_dia = abierto_local < hoy_local

    return {
        "horas": horas,
        "horas_enteras": int(horas),
        "minutos_restantes": int((horas - int(horas)) * 60),
        "de_otro_dia": de_otro_dia,
        "es_largo": horas >= HORAS_TURNO_LARGO or de_otro_dia,
        "abierto_en": turno.abierto_en,
    }


def exige_cierre_antes_de_cobrar(turno: Turno, ahora: datetime | None = None) -> bool:
    """
    Si este turno debe cerrarse antes de aceptar un cobro más.

    Solo cuando viene de otra jornada. Un turno largo del mismo día se avisa,
    pero no se frena: el operador puede llevar doce horas legítimas de caseta
    y un vehículo en la rampa no espera a que alguien cuadre la caja.

    No vive dentro de `registrar_salida` a propósito. Ahí afectaría también a
    la API, que existe para integraciones que no operan caja —pensiones, cobro
    con terminal, conciliaciones— y donde este freno no significa nada. El
    problema que resuelve es de la caja, y en la caja se aplica.
    """
    return estado_del_turno(turno, ahora)["de_otro_dia"]

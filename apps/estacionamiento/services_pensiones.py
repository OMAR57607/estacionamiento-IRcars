"""
Casos de uso de la pensión diaria.

Mismo reparto que el resto de la aplicación: aquí se orquesta —transacciones,
bloqueos, adscripción al turno, bitácora— y las cuentas las hace
`domain/pensiones.py`. Las vistas no deciden nada de esto.

Lo que no se escribe nunca en la bitácora: el nombre y el teléfono del
cliente. Son datos personales y la bitácora la leen más personas, durante más
tiempo, que la ficha del pensionado. Para trazar basta la placa y el número de
pensión.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .domain import pensiones as dominio
from .domain.exceptions import (
    ErrorConfiguracion,
    PagoInsuficiente,
    PagoNoCancelable,
    PensionDeBaja,
    PensionNoEncontrada,
    PensionYaExiste,
    PlanNoConfigurado,
    SinTurnoAbierto,
)
from .models import (
    EstadoFolio,
    EstadoPagoPension,
    EstadoPension,
    EstadoTurno,
    Folio,
    PagoPension,
    Pension,
    PlanPension,
    TipoVehiculo,
)

logger = logging.getLogger(__name__)


def _aviso() -> timedelta:
    return timedelta(hours=getattr(settings, "IRCARS_PENSION_AVISO_HORAS", 2))


def _normalizar(placa: str) -> str:
    # La misma regla que `services.normalizar_placa`; se importa tarde para no
    # crear un ciclo entre los dos módulos de servicios.
    from .services import normalizar_placa

    return normalizar_placa(placa)


# --- Consultas ---------------------------------------------------------------


def plan_vigente() -> PlanPension:
    plan = PlanPension.objects.filter(activo=True).first()
    if plan is None:
        raise PlanNoConfigurado(
            "No hay precio de pensión configurado. El perfil administrativo lo "
            "fija en Precio."
        )
    return plan


def plan_vigente_o_nada() -> PlanPension | None:
    return PlanPension.objects.filter(activo=True).first()


def pension_activa_de(placa: str) -> Pension | None:
    """
    La pensión activa de una placa, o `None`.

    Es la consulta que se hace en cada entrada de vehículo. Va por la placa
    normalizada y la restricción única parcial garantiza que haya a lo sumo
    una.
    """
    return Pension.objects.filter(
        placa=_normalizar(placa), estado=EstadoPension.ACTIVA
    ).first()


def obtener(pension_id: int) -> Pension:
    pension = Pension.objects.filter(pk=pension_id).first()
    if pension is None:
        raise PensionNoEncontrada(f"No existe la pensión {pension_id}.")
    return pension


def vigencia(pension: Pension, ahora: datetime | None = None) -> dominio.EstadoVigencia:
    return dominio.estado_vigencia(
        ahora=ahora or timezone.now(), vigente_hasta=pension.vigente_hasta, aviso=_aviso()
    )


def folio_abierto_de(pension: Pension) -> Folio | None:
    """El folio abierto de la placa de la pensión, si el vehículo está dentro."""
    return Folio.objects.filter(placa=pension.placa, estado=EstadoFolio.ABIERTO).first()


# Orden de la lista: primero lo que requiere atención del operador.
_ORDEN_VIGENCIA = {
    dominio.EstadoVigencia.VENCIDA: 0,
    dominio.EstadoVigencia.SIN_PAGO: 1,
    dominio.EstadoVigencia.POR_VENCER: 2,
    dominio.EstadoVigencia.VIGENTE: 3,
}


def listar(
    *, texto: str = "", filtro: str = "", ahora: datetime | None = None, incluir_bajas: bool = False
) -> list[dict]:
    """
    Pensiones con su vigencia ya resuelta, para la lista.

    `texto` busca sin distinguir mayúsculas en placa, nombre, vehículo y
    teléfono: el operador recuerda a veces la placa, a veces el nombre y a
    veces solo el número desde el que llamaron (ver `_coincide`).

    `filtro` acota por vigencia (`POR_VENCER`, `VENCIDA`, …) o pide las bajas
    (`BAJA`). La vigencia depende del instante y no se guarda, así que ese
    filtro se aplica después de la consulta; con decenas de pensiones el
    costo es despreciable.
    """
    ahora = ahora or timezone.now()
    consulta = Pension.objects.all()
    if filtro == "BAJA":
        consulta = consulta.filter(estado=EstadoPension.BAJA)
    elif not incluir_bajas:
        consulta = consulta.filter(estado=EstadoPension.ACTIVA)

    texto = (texto or "").strip()

    dentro = set(
        Folio.objects.filter(estado=EstadoFolio.ABIERTO).values_list("placa", flat=True)
    )
    filas = []
    for pension in consulta:
        if texto and not _coincide(pension, texto):
            continue
        estado = vigencia(pension, ahora)
        if filtro and filtro != "BAJA" and estado.value != filtro:
            continue
        filas.append(
            {
                "pension": pension,
                "vigencia": estado,
                "dentro": pension.placa in dentro,
            }
        )
    filas.sort(
        key=lambda f: (
            _ORDEN_VIGENCIA[f["vigencia"]],
            f["pension"].vigente_hasta or ahora,
            f["pension"].placa,
        )
    )
    return filas


def _coincide(pension: Pension, texto: str) -> bool:
    """
    Búsqueda sin distinguir mayúsculas, acentos incluidos.

    Se resuelve en Python y no con `icontains` porque SQLite solo pliega
    mayúsculas ASCII: «MÉNDEZ» no encontraría a «Méndez», y el operador no
    tiene por qué saber en qué motor corre la caja. Con decenas o cientos de
    pensiones el recorrido es despreciable.
    """
    buscado = texto.casefold()
    como_placa = texto.upper().replace(" ", "").replace("-", "")
    digitos = "".join(c for c in texto if c.isdigit())
    return (
        como_placa in pension.placa.replace("-", "")
        or buscado in pension.nombre_cliente.casefold()
        or buscado in pension.descripcion_vehiculo.casefold()
        or (bool(digitos) and digitos in pension.telefono)
    )


def conteos(ahora: datetime | None = None) -> dict:
    """Cuántas pensiones activas hay en cada situación. Sin importes."""
    ahora = ahora or timezone.now()
    resultado = {e.value: 0 for e in dominio.EstadoVigencia}
    activas = 0
    for hasta in Pension.objects.filter(estado=EstadoPension.ACTIVA).values_list(
        "vigente_hasta", flat=True
    ):
        activas += 1
        resultado[
            dominio.estado_vigencia(ahora=ahora, vigente_hasta=hasta, aviso=_aviso()).value
        ] += 1
    resultado["ACTIVAS"] = activas
    return resultado


# --- Alta, datos y baja ------------------------------------------------------


@transaction.atomic
def registrar_pension(
    *,
    placa: str,
    tipo_vehiculo: str,
    nombre_cliente: str,
    telefono: str = "",
    descripcion_vehiculo: str = "",
    notas: str = "",
    operador=None,
) -> Pension:
    """
    Caso de uso: alta de una pensión. No cobra.

    El cobro es un acto aparte porque exige turno y efectivo, y el alta no:
    puede registrarse el contrato y cobrarse cuando el cliente pague.
    """
    placa = _normalizar(placa)
    if not placa:
        raise ErrorConfiguracion("La pensión requiere la placa del vehículo.")
    if not (nombre_cliente or "").strip():
        raise ErrorConfiguracion("La pensión requiere el nombre del cliente.")
    if tipo_vehiculo not in TipoVehiculo.values:
        raise ErrorConfiguracion("Tipo de vehículo no reconocido.")
    # Sin precio vigente no tiene sentido dar de alta: el siguiente paso
    # —cobrar— fallaría con el cliente delante.
    plan_vigente()

    if Pension.objects.filter(placa=placa, estado=EstadoPension.ACTIVA).exists():
        raise PensionYaExiste(f"La placa {placa} ya tiene una pensión activa.")

    try:
        # Punto de guardado: si otra caja da de alta la misma placa en el mismo
        # instante, la restricción única de la base rechaza la segunda y la
        # transacción externa sigue utilizable.
        with transaction.atomic():
            pension = Pension.objects.create(
                placa=placa,
                tipo_vehiculo=tipo_vehiculo,
                nombre_cliente=nombre_cliente.strip(),
                telefono=telefono or "",
                descripcion_vehiculo=(descripcion_vehiculo or "").strip(),
                notas=(notas or "").strip(),
                alta_por=operador,
            )
    except IntegrityError as exc:
        raise PensionYaExiste(f"La placa {placa} ya tiene una pensión activa.") from exc

    logger.info(
        "Pensión registrada | pension=%s placa=%s operador=%s",
        pension.pk,
        pension.placa,
        getattr(operador, "username", "sistema"),
    )
    return pension


@transaction.atomic
def editar_datos(
    *,
    pension_id: int,
    nombre_cliente: str,
    telefono: str = "",
    descripcion_vehiculo: str = "",
    notas: str = "",
) -> Pension:
    """
    Corrige los datos de contacto. La placa y el tipo no se editan.

    Cambiar la placa de una pensión con pagos reasignaría esos pagos a otro
    vehículo. Si la placa estaba mal, se da de baja con motivo y se da de alta
    la correcta.
    """
    pension = Pension.objects.select_for_update().filter(pk=pension_id).first()
    if pension is None:
        raise PensionNoEncontrada(f"No existe la pensión {pension_id}.")
    if pension.estado != EstadoPension.ACTIVA:
        raise PensionDeBaja(f"La pensión de {pension.placa} está dada de baja.")
    if not (nombre_cliente or "").strip():
        raise ErrorConfiguracion("La pensión requiere el nombre del cliente.")
    pension.nombre_cliente = nombre_cliente.strip()
    pension.telefono = telefono or ""
    pension.descripcion_vehiculo = (descripcion_vehiculo or "").strip()
    pension.notas = (notas or "").strip()
    pension.save()
    logger.info("Datos de pensión corregidos | pension=%s placa=%s", pension.pk, pension.placa)
    return pension


@transaction.atomic
def dar_de_baja(*, pension_id: int, motivo: str, operador) -> Pension:
    """
    Caso de uso: baja de una pensión. No borra ni reembolsa.

    La placa queda libre para una alta futura. Si el vehículo está dentro con
    una estancia de pensión, la estancia conserva su enlace: al salir se cobra
    lo que exceda la vigencia pagada, como a cualquier pensionado.
    """
    if not (motivo or "").strip():
        raise ErrorConfiguracion("La baja de una pensión requiere el motivo.")
    pension = Pension.objects.select_for_update().filter(pk=pension_id).first()
    if pension is None:
        raise PensionNoEncontrada(f"No existe la pensión {pension_id}.")
    if pension.estado != EstadoPension.ACTIVA:
        raise PensionDeBaja(f"La pensión de {pension.placa} ya estaba dada de baja.")
    pension.estado = EstadoPension.BAJA
    pension.baja_en = timezone.now()
    pension.baja_por = operador
    pension.motivo_baja = motivo.strip()
    pension.save()
    logger.warning(
        "Pensión dada de baja | pension=%s placa=%s operador=%s",
        pension.pk,
        pension.placa,
        getattr(operador, "username", "sistema"),
    )
    return pension


# --- Cobro -------------------------------------------------------------------


def cotizar_pago(
    pension: Pension, *, dias: int = 1, ahora: datetime | None = None
) -> dict:
    """
    Lo que cubriría y costaría un pago, sin cobrar nada.

    Es lo que ve el operador antes de recibir el efectivo: el periodo que se
    pagaría y el monto. Usa exactamente la regla del cobro, de modo que lo que
    se informa es lo que se cobra.
    """
    ahora = ahora or timezone.now()
    plan = plan_vigente()
    folio = folio_abierto_de(pension)
    desde, hasta = dominio.periodo_siguiente(
        ahora=ahora,
        vigente_hasta=pension.vigente_hasta,
        dias=dias,
        inicio_estancia=folio.entrada_en if folio else None,
    )
    return {
        "plan": plan,
        "dias": dias,
        "desde": desde,
        "hasta": hasta,
        "monto": dominio.monto_del_periodo(plan.precio_diario, dias),
        "cubre_estancia": folio is not None,
    }


@transaction.atomic
def cobrar_pension(
    *,
    pension_id: int,
    dias: int,
    pago_recibido: Decimal | None,
    operador,
    momento: datetime | None = None,
) -> PagoPension:
    """
    Caso de uso: cobro de días de pensión.

    Exige turno abierto —el efectivo entra al corte igual que el de los
    boletos— y respeta el freno del turno de otra jornada. El periodo lo
    calcula el dominio; aquí se bloquea la pensión para que dos cajas no
    cobren el mismo periodo, se copia el precio al pago y se actualiza la
    vigencia.

    Si el vehículo está dentro con una estancia que no era de pensión —entró
    con la pensión vencida o antes del alta—, la estancia pasa a ser de
    pensión: el periodo nuevo la cubre desde que entró. Es lo que hace cumplir
    «a menos que renueve».
    """
    from .services import obtener_turno_abierto
    from .services_caja import exige_cierre_antes_de_cobrar

    momento = momento or timezone.now()
    dominio.validar_dias(dias)

    turno = obtener_turno_abierto(operador)
    if turno is None:
        raise SinTurnoAbierto(
            "No hay un turno de caja abierto. Abra turno antes de cobrar la "
            "pensión: un ingreso sin turno queda fuera del corte."
        )
    if exige_cierre_antes_de_cobrar(turno, momento):
        raise SinTurnoAbierto(
            f"El turno {turno.pk} sigue abierto desde el "
            f"{timezone.localtime(turno.abierto_en):%d/%m a las %H:%M}. Ciérrelo "
            "antes de cobrar: si no, el efectivo de dos días se mezcla en un "
            "mismo corte."
        )

    pension = Pension.objects.select_for_update().filter(pk=pension_id).first()
    if pension is None:
        raise PensionNoEncontrada(f"No existe la pensión {pension_id}.")
    if pension.estado != EstadoPension.ACTIVA:
        raise PensionDeBaja(
            f"La pensión de {pension.placa} está dada de baja y no admite cobros."
        )

    plan = plan_vigente()
    folio = (
        Folio.objects.select_for_update()
        .filter(placa=pension.placa, estado=EstadoFolio.ABIERTO)
        .first()
    )
    desde, hasta = dominio.periodo_siguiente(
        ahora=momento,
        vigente_hasta=pension.vigente_hasta,
        dias=dias,
        inicio_estancia=folio.entrada_en if folio else None,
    )
    monto = dominio.monto_del_periodo(plan.precio_diario, dias)

    cambio = None
    if pago_recibido is not None:
        if pago_recibido < monto:
            raise PagoInsuficiente(
                f"El pago recibido (${pago_recibido}) es menor al monto de la "
                f"pensión (${monto})."
            )
        cambio = pago_recibido - monto

    pago = PagoPension.objects.create(
        pension=pension,
        periodo_desde=desde,
        periodo_hasta=hasta,
        dias=dias,
        monto=monto,
        pago_recibido=pago_recibido,
        cambio=cambio,
        plan=plan,
        plan_nombre=plan.nombre,
        plan_precio_diario=plan.precio_diario,
        turno=turno,
        operador=operador,
        creado_en=momento,
    )
    pension.vigente_hasta = hasta
    pension.save(update_fields=["vigente_hasta"])

    if folio is not None and folio.pension_id is None:
        folio.pension = pension
        folio.save(update_fields=["pension", "actualizado_en"])
        logger.info(
            "Estancia pasada a pensión | folio=%s pension=%s", folio.uuid, pension.pk
        )

    logger.info(
        "Pensión cobrada | pension=%s placa=%s dias=%s monto=%s hasta=%s turno=%s",
        pension.pk,
        pension.placa,
        dias,
        monto,
        hasta.isoformat(),
        turno.pk,
    )
    return pago


def _recalcular_vigencia(pension: Pension) -> None:
    pension.vigente_hasta = pension.pagos.filter(
        estado=EstadoPagoPension.VIGENTE
    ).aggregate(hasta=Max("periodo_hasta"))["hasta"]
    pension.save(update_fields=["vigente_hasta"])


@transaction.atomic
def cancelar_pago(*, pago_id: int, motivo: str, operador) -> PagoPension:
    """
    Caso de uso: cancelación de un pago de pensión.

    Solo el último pago vigente de la pensión, y solo mientras su turno siga
    abierto. Cancelar uno intermedio dejaría un hueco en la vigencia que nadie
    pagó ni pidió; cancelar uno de un turno ya cortado alteraría un corte
    firmado, que se corrige con un ajuste y no borrando el ingreso.
    """
    if not (motivo or "").strip():
        raise ErrorConfiguracion("La cancelación de un pago requiere el motivo.")
    pago = (
        PagoPension.objects.select_for_update()
        .select_related("turno", "pension")
        .filter(pk=pago_id)
        .first()
    )
    if pago is None:
        raise PagoNoCancelable(f"No existe el pago {pago_id}.")
    if pago.estado != EstadoPagoPension.VIGENTE:
        raise PagoNoCancelable("Ese pago ya estaba cancelado.")
    if pago.turno.estado != EstadoTurno.ABIERTO:
        raise PagoNoCancelable(
            f"El turno {pago.turno_id} de ese pago ya se cortó. Si el cobro estuvo "
            "mal, corríjalo con un ajuste del corte."
        )
    ultimo = (
        pago.pension.pagos.filter(estado=EstadoPagoPension.VIGENTE)
        .order_by("-periodo_hasta", "-pk")
        .first()
    )
    if ultimo.pk != pago.pk:
        raise PagoNoCancelable(
            "Solo se cancela el último pago de la pensión: cancelar uno anterior "
            "dejaría días sin cubrir en medio."
        )

    pago.estado = EstadoPagoPension.CANCELADO
    pago.cancelado_en = timezone.now()
    pago.cancelado_por = operador
    pago.motivo_cancelacion = motivo.strip()
    pago.save()
    _recalcular_vigencia(pago.pension)

    logger.warning(
        "Pago de pensión cancelado | pago=%s pension=%s monto=%s turno=%s operador=%s",
        pago.uuid,
        pago.pension_id,
        pago.monto,
        pago.turno_id,
        getattr(operador, "username", "sistema"),
    )
    return pago


@transaction.atomic
def cambiar_precio(*, precio_diario: Decimal, operador=None) -> PlanPension:
    """
    Pone en vigencia un precio nuevo y desactiva el anterior.

    Los pagos ya hechos conservan el precio con que se cobraron: cada uno lo
    lleva copiado. El precio nuevo rige desde el siguiente cobro.
    """
    if precio_diario is None or precio_diario <= 0:
        raise ErrorConfiguracion("El precio diario de la pensión debe ser mayor a cero.")
    ahora = timezone.now()
    PlanPension.objects.filter(activo=True).update(activo=False)
    plan = PlanPension.objects.create(
        nombre=f"Pensión diaria {ahora:%Y-%m-%d %H:%M}",
        precio_diario=precio_diario,
        activo=True,
        vigente_desde=ahora,
    )
    logger.warning(
        "Precio de pensión cambiado | plan=%s precio=%s operador=%s",
        plan.pk,
        precio_diario,
        getattr(operador, "username", "sistema"),
    )
    return plan


# --- Corte y reportes --------------------------------------------------------


def importes_del_turno(turno) -> list[Decimal]:
    """Montos de los pagos de pensión vigentes del turno. Base del corte."""
    return list(
        PagoPension.objects.filter(turno=turno, estado=EstadoPagoPension.VIGENTE)
        .order_by("creado_en")
        .values_list("monto", flat=True)
    )


def pagos_del_turno(turno):
    return (
        PagoPension.objects.select_related("pension")
        .filter(turno=turno, estado=EstadoPagoPension.VIGENTE)
        .order_by("creado_en")
    )

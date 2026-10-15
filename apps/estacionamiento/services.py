"""
Capa de aplicación: casos de uso del estacionamiento.

Aquí reside la orquestación —transacciones, bloqueos, selección de cajón,
registro de auditoría—, mientras el cálculo del importe permanece en
`domain/tarifas.py`. Esta separación es deliberada: el controlador HTTP no
contiene lógica de negocio, y la lógica de negocio no conoce HTTP. El mismo
servicio podrá ser invocado mañana por un panel de caja en Streamlit o por el
controlador de la pluma, sin duplicar una sola regla.

Nota sobre concurrencia: un estacionamiento con dos casetas de entrada puede
registrar dos vehículos en el mismo instante. La asignación de cajón usa
`select_for_update(skip_locked=True)`, de modo que cada transacción toma un
cajón distinto sin bloquearse mutuamente, y las restricciones únicas del
modelo actúan como última línea de defensa.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .domain.exceptions import (
    ErrorConfiguracion,
    FolioNoEncontrado,
    FolioYaCerrado,
    PagoInsuficiente,
    SinCajonesDisponibles,
    VehiculoYaDentro,
)
from .domain.tarifas import Cotizacion, cotizar
from .models import Cajon, EstadoFolio, Folio, Tarifa, TipoCajon

logger = logging.getLogger(__name__)

# Placas nacionales y extranjeras: alfanuméricas, admiten guion. Se valida el
# formato de forma permisiva a propósito: rechazar una placa válida impediría
# el ingreso de un cliente, un costo operativo mayor que aceptar una atípica.
PATRON_PLACA = re.compile(r"^[A-Z0-9\-]{3,15}$")

SEGUNDOS_POR_MINUTO = 60


def normalizar_placa(placa: str) -> str:
    """
    Homogeneiza la placa: mayúsculas, sin espacios ni caracteres de relleno.

    Garantiza que la búsqueda y la detección de duplicados sean insensibles a
    mayúsculas y a la forma en que el operador capturó el dato.
    """
    normalizada = (placa or "").strip().upper().replace(" ", "")
    if not PATRON_PLACA.match(normalizada):
        raise ErrorConfiguracion(
            "Placa inválida: se esperan de 3 a 15 caracteres alfanuméricos."
        )
    return normalizada


def obtener_tarifa_vigente(momento: datetime | None = None) -> Tarifa:
    """
    Recupera la tarifa activa cuya vigencia ya inició, la más reciente.

    Si no existe ninguna, se interrumpe la operación: cobrar sin tarifa
    exhibida y registrada no es admisible ni contable ni normativamente.
    """
    momento = momento or timezone.now()
    tarifa = (
        Tarifa.objects.filter(activa=True, vigente_desde__lte=momento)
        .order_by("-vigente_desde")
        .first()
    )
    if tarifa is None:
        raise ErrorConfiguracion(
            "No hay una tarifa vigente configurada. Registre el cuadro "
            "tarifario antes de operar."
        )
    return tarifa


def _minutos_transcurridos(entrada: datetime, salida: datetime) -> int:
    """
    Minutos completos entre entrada y salida.

    Los segundos se descartan (truncamiento, no redondeo) en beneficio del
    cliente: no se cobra un minuto que no transcurrió por completo.
    """
    segundos = (salida - entrada).total_seconds()
    if segundos < 0:
        raise ErrorConfiguracion(
            "La hora de salida es anterior a la de entrada. Verifique el reloj "
            "del equipo: una desincronización produce cobros incorrectos."
        )
    return int(segundos // SEGUNDOS_POR_MINUTO)


@transaction.atomic
def registrar_entrada(
    *,
    placa: str,
    tipo_cajon: str = TipoCajon.AUTOMOVIL,
    operador=None,
    momento: datetime | None = None,
    observaciones: str = "",
) -> Folio:
    """
    Caso de uso: ingreso de un vehículo.

    Asigna el primer cajón libre del tipo solicitado y abre el folio con la
    tarifa vigente. Es atómico: o se asigna el cajón y se emite el folio, o no
    ocurre nada. Un folio sin cajón, o un cajón ocupado sin folio, serían
    estados inconsistentes imposibles de reconciliar en el corte de caja.
    """
    momento = momento or timezone.now()
    placa = normalizar_placa(placa)

    if Folio.objects.filter(placa=placa, estado=EstadoFolio.ABIERTO).exists():
        raise VehiculoYaDentro(
            f"La placa {placa} ya tiene un folio abierto. Registre primero su "
            "salida o verifique si se trata de una placa duplicada."
        )

    tarifa = obtener_tarifa_vigente(momento)

    # Subconsulta de cajones ocupados: un cajón está libre si no participa en
    # ningún folio abierto.
    ocupados = Folio.objects.filter(estado=EstadoFolio.ABIERTO).values("cajon_id")
    cajon = (
        Cajon.objects.filter(activo=True, tipo=tipo_cajon)
        .exclude(id__in=ocupados)
        .order_by("codigo")
        # skip_locked: si otra caseta ya tomó este cajón, se pasa al siguiente
        # en lugar de esperar. Evita que una entrada bloquee a la otra.
        .select_for_update(skip_locked=True)
        .first()
    )
    if cajon is None:
        raise SinCajonesDisponibles(
            f"No hay cajones disponibles del tipo {tipo_cajon}."
        )

    folio = Folio.objects.create(
        placa=placa,
        cajon=cajon,
        tarifa=tarifa,
        estado=EstadoFolio.ABIERTO,
        entrada_en=momento,
        operador_entrada=operador,
        observaciones=observaciones,
    )

    logger.info(
        "Entrada registrada | folio=%s placa=%s cajon=%s tarifa=%s",
        folio.uuid,
        folio.placa,
        cajon.codigo,
        tarifa.nombre,
    )
    return folio


def obtener_folio(uuid_folio) -> Folio:
    """Recupera un folio por su identificador público."""
    folio = Folio.objects.select_related("cajon", "tarifa").filter(uuid=uuid_folio).first()
    if folio is None:
        raise FolioNoEncontrado(f"No existe el folio {uuid_folio}.")
    return folio


def cotizar_folio(folio: Folio, momento: datetime | None = None) -> Cotizacion:
    """
    Caso de uso: consulta del importe a pagar, sin efectos secundarios.

    Permite al operador informar el monto al cliente antes de cobrar y cerrar.
    Una cotización nunca modifica el folio.
    """
    momento = momento or timezone.now()
    if folio.estado == EstadoFolio.CERRADO:
        raise FolioYaCerrado(
            f"El folio {folio.uuid} ya fue liquidado el {folio.salida_en:%Y-%m-%d %H:%M}."
        )
    minutos = _minutos_transcurridos(folio.entrada_en, momento)
    return cotizar(minutos, folio.tarifa.a_parametros())


@transaction.atomic
def registrar_salida(
    *,
    uuid_folio,
    pago_recibido: Decimal | None = None,
    operador=None,
    momento: datetime | None = None,
) -> tuple[Folio, Cotizacion]:
    """
    Caso de uso: cobro y salida del vehículo.

    Calcula el importe, valida el pago, copia los parámetros tarifarios al
    folio y lo cierra. El bloqueo de fila evita que dos cajas cobren el mismo
    folio en paralelo y generen un doble cobro.
    """
    momento = momento or timezone.now()

    # select_for_update sin skip_locked: aquí sí se debe esperar, porque el
    # folio es único y la segunda transacción debe ver el estado ya cerrado.
    folio = (
        Folio.objects.select_for_update()
        .select_related("cajon", "tarifa")
        .filter(uuid=uuid_folio)
        .first()
    )
    if folio is None:
        raise FolioNoEncontrado(f"No existe el folio {uuid_folio}.")
    if folio.estado != EstadoFolio.ABIERTO:
        raise FolioYaCerrado(
            f"El folio {folio.uuid} no está abierto (estado actual: {folio.estado})."
        )

    minutos = _minutos_transcurridos(folio.entrada_en, momento)
    cotizacion = cotizar(minutos, folio.tarifa.a_parametros())

    cambio = None
    if pago_recibido is not None:
        if pago_recibido < cotizacion.importe:
            raise PagoInsuficiente(
                f"El pago recibido (${pago_recibido}) es menor al importe a "
                f"cobrar (${cotizacion.importe})."
            )
        cambio = pago_recibido - cotizacion.importe

    parametros = folio.tarifa
    folio.salida_en = momento
    folio.minutos = minutos
    folio.importe = cotizacion.importe
    folio.pago_recibido = pago_recibido
    folio.cambio = cambio
    folio.desglose = cotizacion.desglose()
    # Copia de la tarifa: el importe queda reproducible de forma permanente.
    folio.tarifa_cuota_hora = parametros.cuota_hora
    folio.tarifa_minutos_cortesia = parametros.minutos_cortesia
    folio.tarifa_fraccion_minutos = parametros.fraccion_minutos
    folio.tarifa_cobro_minimo = parametros.cobro_minimo
    folio.tarifa_tope_diario = parametros.tope_diario
    folio.estado = EstadoFolio.CERRADO
    folio.operador_salida = operador
    folio.save()

    logger.info(
        "Salida registrada | folio=%s placa=%s minutos=%s importe=%s cortesia=%s",
        folio.uuid,
        folio.placa,
        minutos,
        cotizacion.importe,
        cotizacion.es_cortesia,
    )
    return folio, cotizacion


@transaction.atomic
def cancelar_folio(*, uuid_folio, motivo: str, operador=None) -> Folio:
    """
    Caso de uso: cancelación administrativa de un folio abierto.

    Previsto para errores de captura (placa equivocada, entrada duplicada).
    Exige un motivo: una cancelación sin justificación documentada es un hueco
    de control interno y una vía de fuga de ingresos.
    """
    if not motivo or not motivo.strip():
        raise ErrorConfiguracion("La cancelación requiere un motivo documentado.")

    folio = Folio.objects.select_for_update().filter(uuid=uuid_folio).first()
    if folio is None:
        raise FolioNoEncontrado(f"No existe el folio {uuid_folio}.")
    if folio.estado != EstadoFolio.ABIERTO:
        raise FolioYaCerrado(
            f"Solo se cancelan folios abiertos (estado actual: {folio.estado})."
        )

    folio.estado = EstadoFolio.CANCELADO
    folio.observaciones = (
        f"{folio.observaciones}\n[CANCELADO] {motivo.strip()}".strip()
    )
    folio.operador_salida = operador
    folio.save()

    logger.warning(
        "Folio cancelado | folio=%s placa=%s operador=%s motivo=%s",
        folio.uuid,
        folio.placa,
        getattr(operador, "username", "sistema"),
        motivo.strip(),
    )
    return folio


def consultar_ocupacion() -> dict:
    """
    Estado de ocupación en tiempo real, desglosado por tipo de cajón.

    Alimenta tanto el tablero del operador como el letrero de "LLENO" en el
    acceso. Se resuelve en dos consultas agregadas, no en un recorrido en
    memoria, para que el costo no crezca con el tamaño del estacionamiento.
    """
    from django.db.models import Count

    capacidad = {
        fila["tipo"]: fila["total"]
        for fila in Cajon.objects.filter(activo=True)
        .values("tipo")
        .annotate(total=Count("id"))
    }
    ocupacion = {
        fila["cajon__tipo"]: fila["total"]
        for fila in Folio.objects.filter(estado=EstadoFolio.ABIERTO)
        .values("cajon__tipo")
        .annotate(total=Count("id"))
    }

    detalle = []
    for tipo, etiqueta in TipoCajon.choices:
        total = capacidad.get(tipo, 0)
        ocupados = ocupacion.get(tipo, 0)
        detalle.append(
            {
                "tipo": tipo,
                "etiqueta": etiqueta,
                "capacidad": total,
                "ocupados": ocupados,
                "disponibles": max(0, total - ocupados),
                "porcentaje_ocupacion": round(ocupados / total * 100, 1) if total else 0.0,
            }
        )

    capacidad_total = sum(capacidad.values())
    ocupados_total = sum(ocupacion.values())
    return {
        "capacidad_total": capacidad_total,
        "ocupados_total": ocupados_total,
        "disponibles_total": max(0, capacidad_total - ocupados_total),
        "lleno": capacidad_total > 0 and ocupados_total >= capacidad_total,
        "detalle": detalle,
    }

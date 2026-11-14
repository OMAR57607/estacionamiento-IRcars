"""
Motor de la pensión diaria.

Lógica pura, como el motor de tarifas y el de corte: no conoce Django, el ORM
ni el reloj. Recibe instantes y devuelve instantes, importes y veredictos, de
modo que cada regla se verifica en milisegundos y sin base de datos.

Las reglas son las que confirmó el dueño (docs/REDISENO.md §2):

* **Un día de pensión son 24 horas** desde que empieza el periodo, no un día
  natural. Medirlo por fecha regalaría o quitaría horas según la hora del
  pago, y el cliente que paga a las 22:00 se quedaría con dos horas.
* **Si la estancia rebasa la vigencia en un minuto completo, el excedente se
  cobra con la tarifa por hora.** Solo el excedente: cobrar la estancia entera
  por haberse pasado un minuto sería un cargo por algo que ya se pagó, y eso no
  se sostiene ante una aclaración.
* **Renovar no deja huecos.** Si el vehículo sigue dentro, el periodo nuevo
  empieza donde acabó el anterior, y no al pagar: si no, el cliente pagaría
  la pensión y además las horas entre el vencimiento y el pago.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from .exceptions import ErrorConfiguracion
from .tarifas import Cotizacion, ParametrosTarifa, cotizar, minutos_transcurridos

CENTAVO = Decimal("0.01")

# Días que se pueden pagar de una vez. Un mes alcanza para el cliente que paga
# por adelantado; más que eso es casi siempre un error de dedo que deja una
# pensión pagada hasta el año siguiente.
MAXIMO_DIAS = 31

# Ventana de aviso por omisión: en sus últimas horas la pensión se marca «Por
# vencer», para que el operador ofrezca renovar mientras el cliente está ahí.
AVISO_POR_OMISION = timedelta(hours=2)


def _redondear(monto: Decimal) -> Decimal:
    return monto.quantize(CENTAVO, rounding=ROUND_HALF_UP)


class EstadoVigencia(str, Enum):
    """
    En qué situación está una pensión en un instante dado.

    `SIN_PAGO` es distinto de `VENCIDA` a propósito: un alta que nunca se cobró
    no es un cliente que dejó de pagar, y la lista debe poder separarlos.
    """

    SIN_PAGO = "SIN_PAGO"
    VIGENTE = "VIGENTE"
    POR_VENCER = "POR_VENCER"
    VENCIDA = "VENCIDA"

    @property
    def etiqueta(self) -> str:
        return {
            "SIN_PAGO": "Sin pago",
            "VIGENTE": "Vigente",
            "POR_VENCER": "Por vencer",
            "VENCIDA": "Vencida",
        }[self.value]

    @property
    def clase(self) -> str:
        """Clase de la píldora en la interfaz."""
        return self.value.lower().replace("_", "-")


def validar_dias(dias: int) -> int:
    if not isinstance(dias, int) or dias < 1:
        raise ErrorConfiguracion("Se paga al menos un día de pensión.")
    if dias > MAXIMO_DIAS:
        raise ErrorConfiguracion(
            f"No se pueden pagar más de {MAXIMO_DIAS} días de una vez: revise "
            "la cantidad capturada."
        )
    return dias


def periodo_siguiente(
    *,
    ahora: datetime,
    vigente_hasta: datetime | None,
    dias: int,
    inicio_estancia: datetime | None = None,
) -> tuple[datetime, datetime]:
    """
    Periodo que cubre un pago de `dias` hecho en `ahora`.

    `inicio_estancia` es la entrada del vehículo si está dentro en este
    momento. Es lo que hace cumplir «a menos que renueve»: el vehículo que se
    pasó de su vigencia y renueva antes de salir queda cubierto desde el
    vencimiento —o desde que entró, si entró ya vencido— y no paga horas
    sueltas además de la pensión.

    Tres casos, en este orden:

    1. Vigente: el periodo nuevo empieza donde acaba el actual. Pagar antes de
       tiempo no hace perder horas.
    2. Vencida o sin pago, con el vehículo dentro: empieza al vencimiento o a
       la entrada, lo que ocurrió después. Nunca antes de la entrada: no se
       cobra pensión por un tiempo en que el vehículo no estaba.
    3. Vencida o sin pago, con el vehículo fuera: empieza al pagar.
    """
    validar_dias(dias)
    if vigente_hasta is not None and vigente_hasta > ahora:
        desde = vigente_hasta
    elif inicio_estancia is not None:
        desde = (
            inicio_estancia
            if vigente_hasta is None
            else max(inicio_estancia, vigente_hasta)
        )
    else:
        desde = ahora
    return desde, desde + timedelta(days=dias)


def cubre(*, momento: datetime, vigente_hasta: datetime | None) -> bool:
    """
    Si la pensión cubre el instante `momento`.

    El límite es exclusivo: la pensión pagada hasta las 10:00 cubre las 9:59 y
    no las 10:00. Así dos periodos consecutivos no se solapan ni dejan un
    instante sin cubrir.
    """
    return vigente_hasta is not None and momento < vigente_hasta


def estado_vigencia(
    *,
    ahora: datetime,
    vigente_hasta: datetime | None,
    aviso: timedelta = AVISO_POR_OMISION,
) -> EstadoVigencia:
    if vigente_hasta is None:
        return EstadoVigencia.SIN_PAGO
    if vigente_hasta <= ahora:
        return EstadoVigencia.VENCIDA
    if vigente_hasta - ahora <= aviso:
        return EstadoVigencia.POR_VENCER
    return EstadoVigencia.VIGENTE


def monto_del_periodo(precio_diario: Decimal, dias: int) -> Decimal:
    validar_dias(dias)
    if precio_diario <= 0:
        raise ErrorConfiguracion("El precio diario de la pensión debe ser mayor a cero.")
    return _redondear(Decimal(precio_diario) * Decimal(dias))


def minutos_excedentes(
    *, entrada: datetime, salida: datetime, vigente_hasta: datetime | None
) -> int:
    """
    Minutos completos de la estancia que la pensión no cubre.

    Se cuentan desde el vencimiento, o desde la entrada si el vehículo entró
    ya vencido. Los segundos se truncan, como en todo cobro: pasarse 59
    segundos no es pasarse un minuto.
    """
    inicio = entrada if vigente_hasta is None else max(entrada, vigente_hasta)
    if salida <= inicio:
        return 0
    return minutos_transcurridos(inicio, salida)


@dataclass(frozen=True)
class SalidaPension:
    """
    Lo que se cobra al salir un vehículo en pensión.

    `cotizacion` es la del excedente; su `importe` es cero cuando la estancia
    quedó entera dentro de la vigencia. `minutos_totales` es la permanencia
    real, que va al folio y al comprobante.
    """

    minutos_totales: int
    minutos_excedentes: int
    vigente_hasta: datetime | None
    cotizacion: Cotizacion

    @property
    def importe(self) -> Decimal:
        return self.cotizacion.importe

    @property
    def cubierta(self) -> bool:
        return self.minutos_excedentes == 0

    def desglose(self, zona=None) -> dict:
        """Desglose serializable: base del comprobante del folio."""
        return {
            "regla": "pension",
            "minutos_totales": self.minutos_totales,
            "minutos_excedentes": self.minutos_excedentes,
            "vigente_hasta": self.vigente_hasta.isoformat() if self.vigente_hasta else None,
            **self.cotizacion.desglose(),
        }


def parametros_de_excedente(parametros: ParametrosTarifa) -> ParametrosTarifa:
    """
    La tarifa por hora tal como se aplica al excedente: sin cortesía.

    La cortesía existe para la maniobra de quien entra y sale. El excedente de
    una pensión no es eso: es tiempo pagado que se acabó, y la regla del dueño
    es que el primer minuto completo ya se cobra.
    """
    return replace(parametros, minutos_cortesia=0)


def cotizar_salida(
    *,
    entrada: datetime,
    salida: datetime,
    vigente_hasta: datetime | None,
    parametros: ParametrosTarifa,
) -> SalidaPension:
    excedente = minutos_excedentes(
        entrada=entrada, salida=salida, vigente_hasta=vigente_hasta
    )
    if excedente == 0:
        cotizacion = Cotizacion(
            minutos=0,
            importe=Decimal("0.00"),
            es_cortesia=False,
            bloques_cobrados=0,
            costo_por_bloque=Decimal("0.00"),
            tope_diario_aplicado=False,
            cobro_minimo_aplicado=False,
        )
    else:
        cotizacion = cotizar(excedente, parametros_de_excedente(parametros))
    return SalidaPension(
        minutos_totales=max(0, minutos_transcurridos(entrada, salida)),
        minutos_excedentes=excedente,
        vigente_hasta=vigente_hasta,
        cotizacion=cotizacion,
    )

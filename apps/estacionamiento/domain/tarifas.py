"""
Motor de tarificación.

Esta es la pieza más sensible del sistema: determina cuánto se cobra. Por ello
se implementa como **lógica pura** —sin Django, sin ORM, sin reloj propio—, de
modo que sea verificable al 100 % con pruebas unitarias y auditable ante una
aclaración del cliente o una verificación de la autoridad.

Criterio normativo aplicado (PROFECO / cobro proporcional):
  * El cobro se calcula sobre el tiempo **real** de permanencia.
  * El bloque mínimo de cobro (`fraccion_minutos`) es configurable y, por
    omisión, es de 1 minuto: proporcionalidad estricta.
  * Los parámetros nunca están fijos en el código; viven en base de datos,
    con vigencia y registro histórico, y se copian al folio en el momento del
    cobro para que el importe sea reproducible aunque la tarifa cambie después.

Todo el dinero se maneja con `Decimal`. El uso de `float` para importes
monetarios introduce errores de representación y es inaceptable aquí.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .exceptions import ErrorConfiguracion

# Dos decimales: la unidad mínima del peso mexicano.
CENTAVO = Decimal("0.01")

MINUTOS_POR_HORA = 60
MINUTOS_POR_DIA = 1_440


def _redondear(monto: Decimal) -> Decimal:
    """Redondea a centavos con ROUND_HALF_UP (redondeo comercial habitual)."""
    return monto.quantize(CENTAVO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ParametrosTarifa:
    """
    Fotografía inmutable de una tarifa en el instante del cálculo.

    Se construye a partir del registro de base de datos, pero el motor no
    conoce ese origen: recibe únicamente valores. Esa frontera es la que hace
    testeable el cálculo sin levantar una base de datos.
    """

    cuota_hora: Decimal
    minutos_cortesia: int = 0
    fraccion_minutos: int = 1
    cobro_minimo: Decimal = Decimal("0.00")
    tope_diario: Decimal | None = None

    def __post_init__(self) -> None:
        # Validar aquí evita que una tarifa mal capturada en el panel de
        # administración produzca cobros erróneos de forma silenciosa.
        if self.cuota_hora < 0:
            raise ErrorConfiguracion("La cuota por hora no puede ser negativa.")
        if self.minutos_cortesia < 0:
            raise ErrorConfiguracion("Los minutos de cortesía no pueden ser negativos.")
        if self.fraccion_minutos < 1:
            raise ErrorConfiguracion(
                "La fracción de cobro debe ser de al menos 1 minuto."
            )
        if self.fraccion_minutos > MINUTOS_POR_HORA:
            raise ErrorConfiguracion(
                "La fracción de cobro no puede exceder 60 minutos: un bloque mayor "
                "a una hora rompe la proporcionalidad del cobro."
            )
        if self.cobro_minimo < 0:
            raise ErrorConfiguracion("El cobro mínimo no puede ser negativo.")
        if self.tope_diario is not None and self.tope_diario < 0:
            raise ErrorConfiguracion("El tope diario no puede ser negativo.")


@dataclass(frozen=True)
class Cotizacion:
    """
    Resultado desglosado del cálculo.

    El desglose no es un lujo: es lo que permite imprimir un comprobante
    explicable y defender el importe ante una inconformidad.
    """

    minutos: int
    importe: Decimal
    es_cortesia: bool
    bloques_cobrados: int
    costo_por_bloque: Decimal
    tope_diario_aplicado: bool
    cobro_minimo_aplicado: bool

    def desglose(self) -> dict:
        """Representación serializable, apta para API y para comprobante."""
        return {
            "minutos": self.minutos,
            "importe": str(self.importe),
            "es_cortesia": self.es_cortesia,
            "bloques_cobrados": self.bloques_cobrados,
            "costo_por_bloque": str(self.costo_por_bloque),
            "tope_diario_aplicado": self.tope_diario_aplicado,
            "cobro_minimo_aplicado": self.cobro_minimo_aplicado,
        }


def cotizar(minutos: int, parametros: ParametrosTarifa) -> Cotizacion:
    """
    Calcula el importe correspondiente a `minutos` de permanencia.

    Orden de aplicación de las reglas (el orden importa y es intencional):
      1. Cortesía: dentro de la tolerancia el importe es cero.
      2. Proporcional: bloques completos de `fraccion_minutos`, redondeando
         hacia arriba el bloque iniciado.
      3. Cobro mínimo: piso del importe, nunca aplicable a una cortesía.
      4. Tope diario: techo por cada día natural iniciado de permanencia.

    El tope se evalúa al final porque debe acotar el resultado de todo lo
    anterior, incluido el cobro mínimo.
    """
    if minutos < 0:
        raise ErrorConfiguracion(
            "La permanencia no puede ser negativa: revise las marcas de tiempo "
            "de entrada y salida del folio."
        )

    # 1. Periodo de cortesía ------------------------------------------------
    if minutos <= parametros.minutos_cortesia:
        return Cotizacion(
            minutos=minutos,
            importe=Decimal("0.00"),
            es_cortesia=True,
            bloques_cobrados=0,
            costo_por_bloque=Decimal("0.00"),
            tope_diario_aplicado=False,
            cobro_minimo_aplicado=False,
        )

    # 2. Cobro proporcional por fracción ------------------------------------
    # El costo del bloque se deriva de la cuota horaria; así existe una sola
    # fuente de verdad del precio y no hay riesgo de que ambos se desfasen.
    costo_por_bloque = parametros.cuota_hora * Decimal(
        parametros.fraccion_minutos
    ) / Decimal(MINUTOS_POR_HORA)

    # `ceil`: un bloque iniciado se cobra completo. Con fraccion_minutos = 1
    # esto equivale al cobro por minuto real.
    bloques = math.ceil(minutos / parametros.fraccion_minutos)
    importe = costo_por_bloque * Decimal(bloques)

    # 3. Cobro mínimo --------------------------------------------------------
    cobro_minimo_aplicado = False
    if importe < parametros.cobro_minimo:
        importe = parametros.cobro_minimo
        cobro_minimo_aplicado = True

    # 4. Tope diario ---------------------------------------------------------
    tope_aplicado = False
    if parametros.tope_diario is not None:
        dias_iniciados = max(1, math.ceil(minutos / MINUTOS_POR_DIA))
        techo = parametros.tope_diario * Decimal(dias_iniciados)
        if importe > techo:
            importe = techo
            tope_aplicado = True
            # El tope sustituye al mínimo: si ambos compiten, gana el techo.
            cobro_minimo_aplicado = False

    return Cotizacion(
        minutos=minutos,
        importe=_redondear(importe),
        es_cortesia=False,
        bloques_cobrados=bloques,
        costo_por_bloque=_redondear(costo_por_bloque),
        tope_diario_aplicado=tope_aplicado,
        cobro_minimo_aplicado=cobro_minimo_aplicado,
    )

"""
Qué tarifa exhibe el boleto de un folio.

El archivo se llamaba así porque construía la «instantánea» que la caja local
enviaba a la nube. Esa publicación se retiró con la arquitectura que la
justificaba; lo que queda es la regla que decide qué precio se muestra, y
vive aparte porque tiene efecto económico: la usan la vista pública y la
presentación del boleto, y si cada una la resolviera por su cuenta, el mismo
boleto podría mostrar precios distintos según por dónde se consultara.
"""
from __future__ import annotations

from decimal import Decimal


def parametros_exhibidos(folio):
    """
    Parámetros tarifarios que debe mostrar el boleto de este folio.

    Se toman de la copia grabada en el folio cuando ya está liquidado, y de la
    tarifa vigente al ingresar cuando sigue abierto. El orden no es arbitrario:
    en un folio cerrado, esa copia es la que justifica el importe cobrado, y es
    la que debe poder exhibirse ante una aclaración.

    `folio.tarifa` es seguro como fotografía del precio exhibido mientras el
    folio está abierto: el cuadro tarifario es inmutable —un cambio de precio
    crea una tarifa nueva y desactiva la anterior, nunca edita la existente—,
    de modo que esa referencia sigue describiendo lo que el cliente vio al
    entrar aunque el precio haya subido después.

    Vive aquí, y no repetida en cada lector, porque es una regla con efecto
    económico: la usan tanto la instantánea que se publica como la vista
    pública que lee el folio directamente, y si divergieran, el mismo boleto
    mostraría precios distintos según por dónde se consultara.
    """
    from apps.estacionamiento.domain.tarifas import ParametrosTarifa

    if folio.tarifa_cuota_hora is not None:
        return ParametrosTarifa(
            cuota_hora=folio.tarifa_cuota_hora,
            minutos_cortesia=folio.tarifa_minutos_cortesia or 0,
            fraccion_minutos=folio.tarifa_fraccion_minutos or 1,
            minutos_tolerancia=folio.tarifa_minutos_tolerancia or 0,
            cobro_minimo=folio.tarifa_cobro_minimo or Decimal("0.00"),
            tope_diario=folio.tarifa_tope_diario,
        )

    return folio.tarifa.a_parametros()


def instantanea_publica(folio) -> dict:
    """
    Traduce un `Folio` a la carga que verá el cliente en su teléfono.

    Lo que **no** se incluye es tan importante como lo que sí: no viajan la
    placa completa, el operador de entrada o salida, el turno, el pago
    recibido, el cambio entregado, las observaciones internas ni el desglose
    del corte. El servicio de boletos no puede filtrar un dato que nunca
    recibió.

    Qué tarifa se exhibe lo decide `parametros_exhibidos`, compartida con la
    vista pública que lee el folio directamente.
    """
    parametros = parametros_exhibidos(folio)

    return {
        "version": VERSION_CONTRATO,
        "uuid_folio": str(folio.uuid),
        "placa_enmascarada": enmascarar_placa(folio.placa),
        "estado": folio.estado,
        # `cajon_id` en lugar de `folio.cajon` evita una consulta adicional por
        # cada publicación: solo se resuelve el objeto si realmente hay cajón.
        "ubicacion": folio.cajon.codigo if folio.cajon_id else "",
        "entrada_en": _marca(folio.entrada_en),
        "salida_en": _marca(folio.salida_en),
        "minutos": folio.minutos,
        "importe": _texto(folio.importe),
        "cuota_hora": _texto(parametros.cuota_hora),
        "minutos_cortesia": parametros.minutos_cortesia,
        "fraccion_minutos": parametros.fraccion_minutos,
        "minutos_tolerancia": parametros.minutos_tolerancia,
        "cobro_minimo": _texto(parametros.cobro_minimo),
        "tope_diario": _texto(parametros.tope_diario),
    }

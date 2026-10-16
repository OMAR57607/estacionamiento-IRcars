"""
Construcción de la instantánea pública de un folio.

Este módulo define **el contrato de cable** entre la caja y el servicio de
boletos: lo que sale de aquí es, carácter por carácter, lo que valida
`serializers.BoletoIngestaSerializer` al otro extremo. Cualquier cambio obliga
a tocar ambos, y por eso están descritos en el mismo lugar del repositorio.

Una decisión que conviene no perder de vista: la dirección de la dependencia.
`apps.boletos` conoce a `apps.estacionamiento`; **nunca al revés**. El núcleo
del negocio —folios, tarifas, caja, corte— ignora por completo que exista una
publicación en la nube, y debe seguir ignorándolo. Si mañana se retira el
servicio de boletos, basta con desinstalar esta aplicación: no queda un solo
`import` que corregir en el estacionamiento.
"""
from __future__ import annotations

from decimal import Decimal

from .dominio import enmascarar_placa

# Versión del contrato. Viaja en la carga para que el servicio pueda rechazar
# con un mensaje claro a una caja demasiado antigua, en lugar de fallar con un
# error de validación que no explica nada.
VERSION_CONTRATO = 1


def _texto(valor: Decimal | None) -> str | None:
    """
    Los importes viajan como cadena, no como número.

    JSON no distingue entre entero y decimal, y su número es un flotante de
    doble precisión. Serializar `Decimal("0.10")` como número lo convierte en
    un valor que no es exactamente una décima, y el redondeo del importe
    dejaría de ser reproducible entre los dos extremos. Como cadena, el
    `Decimal` se reconstruye idéntico.
    """
    return None if valor is None else str(valor)


def _marca(momento) -> str | None:
    """Fecha y hora en ISO-8601 con zona horaria explícita (siempre UTC)."""
    return None if momento is None else momento.isoformat()


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
        "cobro_minimo": _texto(parametros.cobro_minimo),
        "tope_diario": _texto(parametros.tope_diario),
    }

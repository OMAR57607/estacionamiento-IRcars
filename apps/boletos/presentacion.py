"""
Vista unificada del boleto, sea cual sea su origen.

El boleto que ve el cliente puede venir de dos sitios, según cómo esté
desplegado el sistema:

* **Del folio directamente**, cuando la caja y la consulta pública viven en el
  mismo servicio. Es la arquitectura de la aplicación web: el folio es la
  fuente de verdad y está ahí mismo.
* **De una copia publicada**, cuando la caja es local y envía instantáneas a un
  servicio remoto de solo lectura.

Esta capa existe para que la plantilla, la vigencia, el enmascarado y el
cálculo del importe **se escriban una sola vez**. Sin ella, el mismo boleto
tendría dos implementaciones de presentación y acabarían divergiendo: es el
tipo de duplicación que termina mostrando al cliente un precio distinto según
por dónde consulte.

Nótese qué es `BoletoVista`: un objeto de presentación, no un modelo. Solo
contiene lo que el cliente puede ver. Construirla desde un `Folio` **es** el
punto donde se enmascara la placa y se descarta todo lo demás —operador,
turno, efectivo, observaciones—, de modo que la plantilla nunca llega a tener
acceso a un dato que no deba mostrar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .dominio import enmascarar_placa
from .instantanea import parametros_exhibidos


@dataclass(frozen=True)
class BoletoVista:
    """
    Lo que se puede enseñar de un boleto. Nada más.

    Inmutable a propósito: es el resultado de una decisión de minimización ya
    tomada, y una vista que pudiera modificarse invitaría a «completarla» más
    adelante con datos que no deben salir.

    Los nombres de los campos coinciden con los del modelo `BoletoPublicado`
    para que la plantilla sirva a ambos orígenes sin condicionales.
    """

    uuid_folio: str
    placa_enmascarada: str
    estado: str
    ubicacion: str
    entrada_en: datetime
    salida_en: datetime | None
    minutos: int | None
    importe: Decimal | None

    # Parámetros de la tarifa exhibida al ingresar, para el cuadro del boleto.
    cuota_hora: Decimal
    minutos_cortesia: int
    fraccion_minutos: int
    minutos_tolerancia: int
    cobro_minimo: Decimal
    tope_diario: Decimal | None

    # Momento en que el boleto dejó de estar en curso; `None` si sigue dentro.
    fin_de_ciclo: datetime | None

    @property
    def esta_abierto(self) -> bool:
        return self.estado == "ABIERTO"

    def a_parametros(self):
        from apps.estacionamiento.domain.tarifas import ParametrosTarifa

        return ParametrosTarifa(
            cuota_hora=self.cuota_hora,
            minutos_cortesia=self.minutos_cortesia,
            fraccion_minutos=self.fraccion_minutos,
            minutos_tolerancia=self.minutos_tolerancia,
            cobro_minimo=self.cobro_minimo,
            tope_diario=self.tope_diario,
        )


def desde_folio(folio) -> BoletoVista:
    """
    Construye la vista a partir del folio local.

    Aquí es donde se aplica el enmascarado cuando la caja vive en el mismo
    servicio que la consulta pública. En el despliegue con caja local el
    enmascarado ocurre antes, al publicar; en este, ocurre al presentar. En
    ambos casos pasa por `enmascarar_placa`, que es lo que garantiza que la
    regla —nunca más de la mitad de los caracteres— se cumpla por igual.
    """
    parametros = parametros_exhibidos(folio)

    return BoletoVista(
        uuid_folio=str(folio.uuid),
        placa_enmascarada=enmascarar_placa(folio.placa),
        estado=folio.estado,
        # `cajon_id` evita resolver el objeto cuando no hay cajón asignado.
        ubicacion=folio.cajon.codigo if folio.cajon_id else "",
        entrada_en=folio.entrada_en,
        salida_en=folio.salida_en,
        minutos=folio.minutos,
        importe=folio.importe,
        cuota_hora=parametros.cuota_hora,
        minutos_cortesia=parametros.minutos_cortesia,
        fraccion_minutos=parametros.fraccion_minutos,
        minutos_tolerancia=parametros.minutos_tolerancia,
        cobro_minimo=parametros.cobro_minimo,
        tope_diario=parametros.tope_diario,
        fin_de_ciclo=_fin_de_ciclo(folio.estado, folio.salida_en, folio.actualizado_en),
    )


def _fin_de_ciclo(estado: str, salida_en, actualizado_en):
    """
    Momento en que el boleto dejó de estar en curso.

    No basta con `salida_en`: una cancelación marca el folio como cancelado
    pero **no** registra hora de salida, porque el vehículo no salió por caja.
    Tomar `salida_en` como criterio haría que la página de un boleto cancelado
    no caducara nunca.
    """
    if estado == "ABIERTO":
        return None
    return salida_en or actualizado_en

"""
Pruebas de qué tarifa exhibe el boleto.

El archivo probaba además el contrato de cable que la caja local enviaba a la
nube. Esa publicación se retiró con la arquitectura que la justificaba; lo que
queda es la regla con efecto económico: **al cliente se le exhibe el precio
que vio al ingresar**, y en un folio ya liquidado, la copia que justifica el
importe cobrado.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.boletos.instantanea import parametros_exhibidos
from apps.estacionamiento import services

pytestmark = pytest.mark.django_db


class TestTarifaExhibida:
    def test_folio_abierto_usa_la_tarifa_del_ingreso(self, folio_abierto, tarifa):
        parametros = parametros_exhibidos(folio_abierto)

        assert parametros.cuota_hora == Decimal("30.00")
        assert parametros.minutos_cortesia == 15
        assert parametros.fraccion_minutos == 1

    def test_un_cambio_de_precio_posterior_no_altera_el_boleto(
        self, folio_abierto, tarifa
    ):
        """
        El precio exhibido es el de la entrada, no el vigente ahora.

        Es la obligación que importa ante PROFECO: al cliente se le aplica el
        precio que vio al ingresar. El cuadro tarifario es inmutable —un
        cambio crea una tarifa nueva y desactiva la anterior—, y esta prueba
        fija ese comportamiento para que no se rompa con un `update` cómodo.
        """
        from apps.estacionamiento.models import Tarifa

        Tarifa.objects.filter(activa=True).update(activa=False)
        Tarifa.objects.create(
            nombre="Precio nuevo, más caro",
            cuota_hora=Decimal("90.00"),
            minutos_cortesia=0,
            fraccion_minutos=1,
            activa=True,
            vigente_desde=timezone.now(),
        )

        folio_abierto.refresh_from_db()

        assert parametros_exhibidos(folio_abierto).cuota_hora == Decimal("30.00"), (
            "El boleto debe seguir mostrando el precio exhibido al ingresar."
        )

    def test_folio_cerrado_usa_la_copia_grabada_al_cobrar(
        self, folio_abierto, operador, tarifa
    ):
        """
        En un folio liquidado manda la copia del folio, no la tarifa vigente.

        Esa copia es la que justifica el importe cobrado y la que debe poder
        exhibirse ante una aclaración.
        """
        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )

        parametros = parametros_exhibidos(folio)

        assert parametros.cuota_hora == folio.tarifa_cuota_hora
        assert parametros.minutos_tolerancia == (folio.tarifa_minutos_tolerancia or 0)

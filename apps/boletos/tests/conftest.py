"""
Material común de las pruebas del boleto del cliente.

Las fixtures replican las de `apps.estacionamiento.tests` en lugar de
importarlas: el acoplamiento entre suites de pruebas hace que un cambio en una
rompa la otra por motivos que no tienen que ver con lo que se está probando.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon

URL_PRUEBA = "https://boletos.ejemplo.mx"


@pytest.fixture
def tarifa(db):
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        cobro_minimo=Decimal("0.00"),
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajones(db):
    return [
        Cajon.objects.create(codigo=f"A-{n:02d}", tipo=TipoCajon.AUTOMOVIL)
        for n in range(1, 3)
    ]


@pytest.fixture
def operador(db):
    return get_user_model().objects.create_user(
        username="cajero", password="clave-de-prueba-1234"
    )


@pytest.fixture
def servicio_configurado(settings):
    """Servicio con origen público y nombre comercial definidos."""
    settings.IRCARS_URL_BOLETOS = URL_PRUEBA
    settings.IRCARS_HORAS_VIGENCIA_BOLETO = 24
    settings.IRCARS_NOMBRE_COMERCIAL = "Estacionamiento de Pruebas"
    return settings


@pytest.fixture
def folio_abierto(tarifa, cajones, operador):
    """Folio con una hora de permanencia, suficiente para cotizar algo."""
    folio = services.registrar_entrada(placa="ABC1234", operador=operador)
    folio.entrada_en = timezone.now() - timedelta(hours=1)
    folio.save(update_fields=["entrada_en"])
    return folio


# La limpieza del caché entre pruebas vive en `apps/conftest.py`: afecta a
# todas las suites, no solo a esta.

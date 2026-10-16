"""
Material común de las pruebas de publicación de boletos.

Las fixtures replican las de `apps.estacionamiento.tests` en lugar de
importarlas: el acoplamiento entre suites de pruebas hace que un cambio en una
rompa la otra por motivos que no tienen que ver con lo que se está probando.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.boletos.instantanea import instantanea_publica
from apps.boletos.models import BoletoPublicado
from apps.estacionamiento import services
from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon

# Token de pruebas. No es un secreto real y nunca sale de este proceso.
TOKEN_PRUEBA = "token-de-pruebas-no-es-un-secreto-real-0123456789"
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
    """Servicio de boletos en la nube, con token y origen definidos."""
    settings.IRCARS_TOKEN_PUBLICACION = TOKEN_PRUEBA
    settings.IRCARS_URL_BOLETOS = URL_PRUEBA
    settings.IRCARS_HORAS_VIGENCIA_BOLETO = 24
    settings.IRCARS_NOMBRE_COMERCIAL = "Estacionamiento de Pruebas"
    return settings


@pytest.fixture
def publicacion_encendida(servicio_configurado):
    """
    Caja con la publicación activa.

    Separada de `servicio_configurado` a propósito: la mayoría de las pruebas
    del servicio en la nube deben correr con la publicación *apagada*, que es
    como se despliega allí, y solo las de la caja la encienden.
    """
    servicio_configurado.IRCARS_PUBLICAR_BOLETOS = True
    return servicio_configurado


@pytest.fixture
def folio_abierto(tarifa, cajones, operador):
    """Folio con una hora de permanencia, suficiente para cotizar algo."""
    folio = services.registrar_entrada(placa="ABC1234", operador=operador)
    folio.entrada_en = timezone.now() - timedelta(hours=1)
    folio.save(update_fields=["entrada_en"])
    return folio


@pytest.fixture
def publicar():
    """
    Crea en la «nube» el boleto correspondiente a un folio.

    Atajo que evita pasar por HTTP en las pruebas que solo examinan la vista
    pública. Usa la misma instantánea que enviaría la caja, de modo que no
    puede probar un contrato distinto al real.
    """

    def _publicar(folio) -> BoletoPublicado:
        datos = instantanea_publica(folio)
        datos.pop("version")
        uuid_folio = datos.pop("uuid_folio")
        boleto = BoletoPublicado.objects.create(uuid_folio=uuid_folio, **datos)

        # Releer de la base no es un adorno. La instantánea transporta los
        # importes como cadena —JSON no distingue decimal de flotante—, y un
        # objeto creado en memoria **conserva esas cadenas** en sus campos.
        # En producción eso nunca ocurre: la ingesta escribe y cualquier
        # lectura posterior devuelve `Decimal`. Sin esta relectura el
        # accesorio produciría objetos que no existen en el sistema real, y
        # las pruebas que lo usen estarían comprobando otra cosa.
        boleto.refresh_from_db()
        return boleto

    return _publicar

# La limpieza del caché entre pruebas vive en `apps/conftest.py`: afecta a
# todas las suites, no solo a esta.

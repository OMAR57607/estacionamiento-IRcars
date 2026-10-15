"""
Pruebas del controlador HTTP.

Verifican el contrato de la API: códigos de estado, autenticación obligatoria
y la traducción de los errores de dominio. No reexaminan el cálculo del
importe —eso ya está cubierto en `test_tarifas.py`—, sino que el controlador
delegue correctamente.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.estacionamiento import services
from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifa():
    # Vigencia en el pasado: aplica la tarifa vigente al momento de la entrada.
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajones():
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
def cliente(operador):
    api = APIClient()
    api.force_authenticate(user=operador)
    return api


class TestAutenticacion:
    def test_sin_credenciales_no_se_puede_operar(self, tarifa, cajones):
        api = APIClient()
        respuesta = api.post(reverse("folio-list"), {"placa": "ABC123"}, format="json")
        # Operar la caja exige identidad: cada movimiento queda atribuido.
        assert respuesta.status_code in (401, 403)

    def test_la_sonda_de_salud_es_publica(self):
        # La supervisión del proceso no tiene credenciales y debe poder
        # consultar el estado del servicio.
        assert APIClient().get(reverse("salud")).status_code == 200


class TestEntrada:
    def test_registrar_entrada_devuelve_201_y_el_folio(self, cliente, tarifa, cajones):
        respuesta = cliente.post(
            reverse("folio-list"), {"placa": "abc-123"}, format="json"
        )
        assert respuesta.status_code == 201
        assert respuesta.data["placa"] == "ABC-123"
        assert respuesta.data["estado"] == "ABIERTO"
        assert respuesta.data["cajon_codigo"] == "A-01"

    def test_placa_duplicada_responde_409(self, cliente, tarifa, cajones):
        cliente.post(reverse("folio-list"), {"placa": "ABC123"}, format="json")
        respuesta = cliente.post(
            reverse("folio-list"), {"placa": "ABC123"}, format="json"
        )
        assert respuesta.status_code == 409
        assert respuesta.data["error"] == "VehiculoYaDentro"

    def test_estacionamiento_lleno_responde_409(self, cliente, tarifa, cajones):
        for n in range(len(cajones)):
            cliente.post(reverse("folio-list"), {"placa": f"PLACA{n}"}, format="json")
        respuesta = cliente.post(
            reverse("folio-list"), {"placa": "EXTRA1"}, format="json"
        )
        assert respuesta.status_code == 409
        assert respuesta.data["error"] == "SinCajonesDisponibles"

    def test_placa_invalida_responde_400(self, cliente, tarifa, cajones):
        respuesta = cliente.post(reverse("folio-list"), {"placa": "A"}, format="json")
        assert respuesta.status_code == 400

    def test_se_atribuye_el_movimiento_al_operador(self, cliente, operador, tarifa, cajones):
        respuesta = cliente.post(
            reverse("folio-list"), {"placa": "ABC123"}, format="json"
        )
        folio = services.obtener_folio(respuesta.data["uuid"])
        assert folio.operador_entrada == operador


class TestCotizacionYSalida:
    def test_cotizar_informa_el_importe_sin_cerrar(self, cliente, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=1)
        )
        respuesta = cliente.get(reverse("folio-cotizar", args=[folio.uuid]))
        assert respuesta.status_code == 200
        assert respuesta.data["importe"] == "30.00"
        folio.refresh_from_db()
        assert folio.estado == "ABIERTO"

    def test_salida_cobra_y_cierra(self, cliente, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=2)
        )
        respuesta = cliente.post(
            reverse("folio-salida", args=[folio.uuid]),
            {"pago_recibido": "100.00"},
            format="json",
        )
        assert respuesta.status_code == 200
        assert respuesta.data["folio"]["estado"] == "CERRADO"
        assert respuesta.data["folio"]["importe"] == "60.00"
        assert respuesta.data["folio"]["cambio"] == "40.00"

    def test_pago_insuficiente_responde_400(self, cliente, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=2)
        )
        respuesta = cliente.post(
            reverse("folio-salida", args=[folio.uuid]),
            {"pago_recibido": "10.00"},
            format="json",
        )
        assert respuesta.status_code == 400
        assert respuesta.data["error"] == "PagoInsuficiente"

    def test_folio_inexistente_responde_404(self, cliente, tarifa, cajones):
        respuesta = cliente.post(
            reverse("folio-salida", args=["00000000-0000-0000-0000-000000000000"]),
            {},
            format="json",
        )
        assert respuesta.status_code == 404
        assert respuesta.data["error"] == "FolioNoEncontrado"


class TestConsultas:
    def test_busqueda_por_placa_es_insensible_a_mayusculas(self, cliente, tarifa, cajones):
        services.registrar_entrada(placa="ABC123")
        respuesta = cliente.get(reverse("folio-list"), {"placa": "abc"})
        assert respuesta.status_code == 200
        assert respuesta.data["count"] == 1

    def test_ocupacion_reporta_disponibilidad(self, cliente, tarifa, cajones):
        services.registrar_entrada(placa="ABC123")
        respuesta = cliente.get(reverse("ocupacion"))
        assert respuesta.status_code == 200
        assert respuesta.data["capacidad_total"] == 2
        assert respuesta.data["ocupados_total"] == 1
        assert respuesta.data["lleno"] is False

    def test_el_folio_no_admite_edicion_ni_borrado(self, cliente, tarifa, cajones):
        folio = services.registrar_entrada(placa="ABC123")
        url = reverse("folio-detail", args=[folio.uuid])
        # Un registro contable no se edita ni se elimina: se cancela.
        assert cliente.put(url, {"placa": "OTRA99"}, format="json").status_code == 405
        assert cliente.delete(url).status_code == 405


class TestPermisos:
    def test_un_cajero_no_puede_modificar_la_tarifa(self, cliente, tarifa):
        # El precio es el parámetro más sensible: no se toca desde la caja.
        respuesta = cliente.post(
            reverse("tarifa-list"),
            {"nombre": "Pirata", "cuota_hora": "1.00"},
            format="json",
        )
        assert respuesta.status_code == 403

    def test_un_cajero_si_puede_consultar_cajones(self, cliente, cajones):
        assert cliente.get(reverse("cajon-list")).status_code == 200

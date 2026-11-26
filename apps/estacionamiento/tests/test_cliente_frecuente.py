"""
Aviso de cliente frecuente en la recepción.

Al registrar una entrada, la caja dice si la placa es de un cliente que
vuelve. Lo que se fija aquí es qué cuenta como visita —estancias terminadas,
no canceladas, dentro de la ventana— y que el aviso nunca estorbe la entrada:
no se muestra a quien ya tiene pensión, no expone el gasto en la pantalla que
ve el cliente y no tumba la petición si la consulta falla.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_metricas, services_pensiones
from apps.estacionamiento.domain.exceptions import ErrorConfiguracion
from apps.estacionamiento.models import (
    EstadoFolio,
    Folio,
    PlanPension,
    Tarifa,
    TipoVehiculo,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def tarifa():
    return Tarifa.objects.create(
        nombre="Precio automóvil",
        tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=0,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=90),
    )


@pytest.fixture(autouse=True)
def umbral(settings):
    # Fijado aquí para que las pruebas no dependan del valor por omisión.
    settings.IRCARS_FRECUENTE_DIAS = 30
    settings.IRCARS_FRECUENTE_VISITAS = 3


@pytest.fixture
def sesion(client):
    usuario = get_user_model().objects.create_user(username="cajero", password="x" * 12)
    client.force_login(usuario)
    return client


def _visita(placa, hace_dias, estado=EstadoFolio.CERRADO):
    """Una estancia pasada, ya terminada, de una hora."""
    entrada = timezone.now() - timedelta(days=hace_dias)
    folio = services.registrar_entrada(placa=placa, momento=entrada)
    # Se cierra por la base y no por el cobro: lo que se examina es el conteo,
    # y el cobro exige turno, pago y tarifa, que aquí solo serían ruido.
    Folio.objects.filter(pk=folio.pk).update(
        estado=estado, salida_en=entrada + timedelta(hours=1), importe=Decimal("30.00")
    )
    return folio


class TestQueCuentaComoVisita:
    def test_debajo_del_umbral_no_es_frecuente(self):
        _visita("ABC123", 3)
        _visita("ABC123", 2)
        assert services_metricas.cliente_frecuente("ABC123") is None

    def test_en_el_umbral_es_frecuente(self):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        frecuente = services_metricas.cliente_frecuente("ABC123")
        assert frecuente is not None
        assert frecuente.visitas == 3
        assert frecuente.dias == 30

    def test_la_ultima_visita_es_la_entrada_mas_reciente(self):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        frecuente = services_metricas.cliente_frecuente("ABC123")
        esperado = timezone.now() - timedelta(days=2)
        assert abs(frecuente.ultima_visita - esperado) < timedelta(minutes=1)

    def test_las_visitas_fuera_de_la_ventana_no_cuentan(self):
        _visita("ABC123", 45)
        _visita("ABC123", 40)
        _visita("ABC123", 5)
        assert services_metricas.cliente_frecuente("ABC123") is None

    def test_los_boletos_cancelados_no_son_visitas(self):
        _visita("ABC123", 9, estado=EstadoFolio.CANCELADO)
        _visita("ABC123", 6)
        _visita("ABC123", 3)
        assert services_metricas.cliente_frecuente("ABC123") is None

    def test_el_boleto_abierto_no_cuenta(self):
        _visita("ABC123", 6)
        _visita("ABC123", 3)
        services.registrar_entrada(placa="ABC123")
        assert services_metricas.cliente_frecuente("ABC123") is None

    def test_la_busqueda_no_distingue_mayusculas_ni_espacios(self):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        assert services_metricas.cliente_frecuente(" abc 123 ") is not None

    def test_quien_tiene_pension_activa_no_recibe_el_aviso(self):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        services_pensiones.registrar_pension(
            placa="ABC123", tipo_vehiculo=TipoVehiculo.AUTOMOVIL, nombre_cliente="Cliente"
        )
        assert services_metricas.cliente_frecuente("ABC123") is None

    def test_el_umbral_y_la_ventana_se_configuran(self, settings):
        settings.IRCARS_FRECUENTE_VISITAS = 2
        settings.IRCARS_FRECUENTE_DIAS = 60
        _visita("ABC123", 50)
        _visita("ABC123", 40)
        assert services_metricas.cliente_frecuente("ABC123").visitas == 2

    def test_un_umbral_de_una_visita_se_rechaza(self, settings):
        settings.IRCARS_FRECUENTE_VISITAS = 1
        with pytest.raises(ErrorConfiguracion):
            services_metricas.cliente_frecuente("ABC123")


class TestAvisoEnLaRecepcion:
    def _entrada(self, sesion, placa):
        return sesion.post(
            reverse("web:caja"), {"accion": "entrada", "placa": placa}, follow=True
        )

    def test_la_entrada_de_un_frecuente_lo_avisa_en_el_boleto(self, sesion):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        respuesta = self._entrada(sesion, "abc123")
        contenido = respuesta.content.decode()
        assert respuesta.templates[0].name == "estacionamiento/boleto.html"
        assert "Cliente frecuente: ABC123 lleva 3 visitas" in contenido
        # Etiqueta propia para que se distinga de un aviso informativo cualquiera.
        assert 'class="aviso frecuente info"' in contenido

    def test_con_plan_de_pension_se_ofrece_con_su_precio(self, sesion):
        services_pensiones.cambiar_precio(precio_diario=Decimal("1250.00"))
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        contenido = self._entrada(sesion, "ABC123").content.decode()
        assert "pensión diaria de $1,250.00" in contenido

    def test_sin_plan_de_pension_no_se_ofrece_precio(self, sesion):
        PlanPension.objects.update(activo=False)
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        contenido = self._entrada(sesion, "ABC123").content.decode()
        assert "Cliente frecuente: ABC123" in contenido
        assert "pensión diaria" not in contenido

    def test_un_cliente_nuevo_no_recibe_aviso(self, sesion):
        contenido = self._entrada(sesion, "NUEVO1").content.decode()
        assert "Cliente frecuente" not in contenido
        assert "Entrada registrada" in contenido

    def test_el_aviso_no_expone_el_gasto_del_cliente(self, sesion):
        # La pantalla del boleto se pone delante del cliente.
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        contenido = self._entrada(sesion, "ABC123").content.decode()
        aviso = contenido.split("Cliente frecuente")[1].split("</div>")[0]
        assert "$90" not in aviso
        assert "gast" not in aviso.lower()

    def test_si_la_consulta_falla_la_entrada_sigue(self, sesion):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        with mock.patch.object(
            services_metricas, "cliente_frecuente", side_effect=DatabaseError("caída")
        ):
            respuesta = self._entrada(sesion, "ABC123")
        assert respuesta.status_code == 200
        assert "Entrada registrada" in respuesta.content.decode()
        assert Folio.objects.filter(placa="ABC123", estado=EstadoFolio.ABIERTO).exists()

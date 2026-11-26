"""
Aviso de cliente frecuente en la recepción.

Al registrar una entrada, la caja dice si la placa es de un cliente que
vuelve. Lo que se fija aquí es qué cuenta como visita —estancias terminadas,
no canceladas, dentro de la ventana— y que el aviso nunca estorbe la entrada:
no se muestra a quien ya tiene pensión ni a las motos —que por lo regular no
traen placa—, no expone el gasto en la pantalla que ve el cliente y no tumba
la petición si la consulta falla.
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


@pytest.fixture
def tarifa_moto():
    # La migración siembra el precio de moto vigente desde que corre, y las
    # visitas de estas pruebas son de días atrás. Se sustituye por uno
    # retroactivo; el modelo impide dos precios activos del mismo tipo.
    Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.MOTOCICLETA).update(activa=False)
    return Tarifa.objects.create(
        nombre="Precio motocicleta",
        tipo_vehiculo=TipoVehiculo.MOTOCICLETA,
        cuota_hora=Decimal("10.00"),
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


def _visita(placa, hace_dias, estado=EstadoFolio.CERRADO, tipo_cajon="AUTO"):
    """Una estancia pasada, ya terminada, de una hora."""
    entrada = timezone.now() - timedelta(days=hace_dias)
    # El precio de motocicleta lo siembra su migración; el de automóvil, la
    # fixture `tarifa`.
    folio = services.registrar_entrada(
        placa=placa, momento=entrada, tipo_cajon=tipo_cajon
    )
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


class TestLasMotosNoSonClientesFrecuentes:
    """
    Las motos por lo regular no traen placa: el operador captura un texto de
    relleno que se repite de una moto a otra. Contarlas juntaría motos
    distintas en un solo «cliente» que no existe.
    """

    @pytest.fixture(autouse=True)
    def _precio_moto(self, tarifa_moto):
        return tarifa_moto

    def test_las_estancias_en_moto_no_son_visitas(self):
        for dias in (20, 10, 2):
            _visita("MOTO", dias, tipo_cajon="MOTO")
        assert services_metricas.cliente_frecuente("MOTO") is None

    def test_una_moto_no_completa_el_umbral_de_un_auto(self):
        _visita("ABC123", 20)
        _visita("ABC123", 10)
        _visita("ABC123", 2, tipo_cajon="MOTO")
        assert services_metricas.cliente_frecuente("ABC123") is None


class TestAvisoEnLaRecepcion:
    def _entrada(self, sesion, placa, tipo_cajon="AUTO"):
        return sesion.post(
            reverse("web:caja"),
            {"accion": "entrada", "placa": placa, "tipo_cajon": tipo_cajon},
            follow=True,
        )

    def test_la_entrada_de_una_moto_no_avisa(self, sesion):
        # Aunque la placa capturada coincida con la de un auto frecuente: en
        # una moto, lo más probable es que sea de relleno.
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        contenido = self._entrada(sesion, "ABC123", tipo_cajon="MOTO").content.decode()
        assert "Entrada registrada" in contenido
        assert "Cliente frecuente" not in contenido

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


class TestMarcaEnElPatio:
    def _patio(self, sesion):
        return sesion.get(reverse("web:caja"))

    def _fila(self, contenido, placa):
        return contenido.split(f'data-placa="{placa}"')[1].split("</li>")[0]

    def test_el_frecuente_dentro_lleva_la_marca(self, sesion):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        services.registrar_entrada(placa="ABC123")
        services.registrar_entrada(placa="NUEVO1")
        contenido = self._patio(sesion).content.decode()
        assert "pildora frecuente" in self._fila(contenido, "ABC123")
        assert "3 visitas en los últimos 30 días" in self._fila(contenido, "ABC123")
        assert "pildora frecuente" not in self._fila(contenido, "NUEVO1")

    def test_quien_tiene_pension_activa_no_lleva_la_marca(self, sesion):
        # Pensión dada de alta y sin cobrar: la estancia se cobra por hora,
        # pero el cliente ya es de pensión y la marca no aporta nada.
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        services_pensiones.registrar_pension(
            placa="ABC123", tipo_vehiculo=TipoVehiculo.AUTOMOVIL, nombre_cliente="Cliente"
        )
        services.registrar_entrada(placa="ABC123")
        fila = self._fila(self._patio(sesion).content.decode(), "ABC123")
        assert "pildora frecuente" not in fila

    def test_la_moto_dentro_no_lleva_la_marca(self, sesion):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        services.registrar_entrada(placa="ABC123", tipo_cajon="MOTO")
        fila = self._fila(self._patio(sesion).content.decode(), "ABC123")
        assert "pildora frecuente" not in fila

    def test_todo_el_patio_se_resuelve_en_una_sola_consulta(self, sesion):
        # Una consulta por fila haría crecer el tiempo de la caja con la
        # ocupación. Se cuentan las consultas de la marca, no las de la página.
        for placa in ("AAA111", "BBB222", "CCC333"):
            for dias in (20, 10, 2):
                _visita(placa, dias)
            services.registrar_entrada(placa=placa)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as consultas:
            frecuentes = services_metricas.placas_frecuentes({"AAA111", "BBB222", "CCC333"})
        assert set(frecuentes) == {"AAA111", "BBB222", "CCC333"}
        assert len(consultas) == 1

    def test_si_la_consulta_falla_el_patio_se_dibuja_sin_marca(self, sesion):
        for dias in (20, 10, 2):
            _visita("ABC123", dias)
        services.registrar_entrada(placa="ABC123")
        with mock.patch.object(
            services_metricas, "placas_frecuentes", side_effect=DatabaseError("caída")
        ):
            respuesta = self._patio(sesion)
        assert respuesta.status_code == 200
        assert 'data-placa="ABC123"' in respuesta.content.decode()
        assert "pildora frecuente" not in respuesta.content.decode()

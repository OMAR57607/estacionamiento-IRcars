"""
Pruebas de la caja rediseñada: una placa, un campo.

La captura única sustituye a los dos formularios de entrada y salida. Lo que
se fija aquí es la tabla de resolución de docs/REDISENO.md §7.1: qué responde
el servidor a cada combinación de botón y estado de la placa. Los casos que
antes eran un error seco —entrada de una placa que ya está dentro, cobro de
una que no— deben ofrecer lo que el operador seguramente quería.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja
from apps.estacionamiento.models import EstadoFolio, Folio, Tarifa, TipoVehiculo
from apps.estacionamiento.web.views import CLAVE_RESULTADO_COBRO

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def cajero():
    # El precio de la motocicleta lo siembra la migración 0005; el del
    # automóvil se fija aquí para que los importes sean predecibles.
    Tarifa.objects.create(
        nombre="Precio automóvil",
        tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=0,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def sesion(client, cajero):
    client.force_login(cajero)
    return client


@pytest.fixture
def con_turno(cajero):
    return services_caja.abrir_turno(operador=cajero, fondo_inicial=Decimal("200.00"))


def _dentro(placa, minutos=90, tipo="AUTO"):
    return services.registrar_entrada(
        placa=placa, tipo_cajon=tipo, momento=timezone.now() - timedelta(minutes=minutos)
    )


def _caja(sesion, **datos):
    return sesion.post(reverse("web:caja"), datos)


class TestCapturaUnica:
    def test_la_caja_tiene_un_solo_campo_de_placa(self, sesion):
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert contenido.count('name="placa"') == 1
        assert 'value="entrada"' in contenido
        assert 'value="cotizar"' in contenido

    def test_el_tipo_de_vehiculo_es_un_toque_y_no_un_menu(self, sesion):
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert 'type="radio"' in contenido
        assert "<select" not in contenido

    def test_la_motocicleta_elegida_cobra_su_precio(self, sesion):
        _caja(sesion, accion="entrada", placa="moto12", tipo_cajon="MOTO")
        folio = Folio.objects.get(placa="MOTO12")
        assert folio.tarifa.tipo_vehiculo == TipoVehiculo.MOTOCICLETA

    def test_entrada_nueva_lleva_al_boleto(self, sesion):
        respuesta = _caja(sesion, accion="entrada", placa="abc 123")
        assert respuesta.status_code == 302
        assert "/boleto/" in respuesta["Location"]
        assert Folio.objects.filter(placa="ABC123", estado=EstadoFolio.ABIERTO).exists()

    def test_placa_vacia_se_explica_junto_al_campo(self, sesion):
        respuesta = _caja(sesion, accion="entrada", placa="  ")
        assert respuesta.status_code == 400
        contenido = respuesta.content.decode()
        assert "error-campo" in contenido
        assert "Teclee la placa" in contenido
        assert not Folio.objects.exists()


class TestCasosQueAntesEranError:
    def test_entrada_de_una_placa_que_ya_esta_dentro_ofrece_su_cobro(self, sesion):
        folio = _dentro("XYZ987")
        respuesta = _caja(sesion, accion="entrada", placa="xyz987")

        assert respuesta.status_code == 200
        contenido = respuesta.content.decode()
        assert "ya estaba dentro" in contenido
        assert "Cobro de XYZ987" in contenido
        assert str(folio.uuid) in contenido
        assert Folio.objects.filter(placa="XYZ987").count() == 1

    def test_cobro_de_una_placa_que_no_esta_sugiere_las_parecidas(self, sesion):
        _dentro("NXK4821")
        _dentro("PBC193A")
        contenido = _caja(sesion, accion="cotizar", placa="nxk").content.decode()

        # Solo el bloque del aviso: el patio, más abajo, lista todas.
        aviso = contenido[contenido.index("NXK no está dentro") :]
        aviso = aviso[: aviso.index("Registrar entrada de NXK")]
        assert 'value="NXK4821"' in aviso
        assert 'value="PBC193A"' not in aviso

    def test_las_sugerencias_ignoran_los_guiones(self, sesion):
        _dentro("ABC-1234")
        contenido = _caja(sesion, accion="cotizar", placa="abc12").content.decode()
        aviso = contenido[contenido.index("no está dentro") :]
        assert 'value="ABC-1234"' in aviso[: aviso.index("Registrar entrada")]

    def test_la_busqueda_no_distingue_mayusculas_ni_espacios(self, sesion):
        _dentro("ABC1234")
        contenido = _caja(sesion, accion="cotizar", placa="abc 1234").content.decode()
        assert "Cobro de ABC1234" in contenido


class TestCobro:
    def test_la_tarjeta_de_cobro_ofrece_solo_billetes_que_alcanzan(self, sesion, con_turno):
        _dentro("ABC1234", minutos=150)  # $75.00 a $30/h
        contenido = _caja(sesion, accion="cotizar", placa="ABC1234").content.decode()

        assert "$75.00" in contenido
        assert 'data-monto="75.00"' in contenido
        assert 'data-monto="100"' in contenido
        assert 'data-monto="50"' not in contenido
        assert 'data-importe="75.00"' in contenido

    def test_tras_cobrar_el_cambio_ocupa_la_tarjeta_una_sola_vez(self, sesion, con_turno):
        folio = _dentro("ABC1234", minutos=150)
        respuesta = _caja(
            sesion, accion="cobrar", folio=str(folio.uuid), pago_recibido="100.00"
        )
        assert respuesta["Location"] == reverse("web:caja")

        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert "Cambio a entregar" in contenido
        assert "$25.00" in contenido
        assert "Siguiente vehículo" in contenido

        # Lectura única: recargar no vuelve a mostrar el cobro anterior.
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert "Cambio a entregar" not in contenido

    def test_el_cambio_no_viaja_en_la_direccion(self, sesion, con_turno):
        folio = _dentro("ABC1234", minutos=150)
        respuesta = _caja(
            sesion, accion="cobrar", folio=str(folio.uuid), pago_recibido="100.00"
        )
        assert "25" not in respuesta["Location"]
        assert CLAVE_RESULTADO_COBRO in sesion.session

    def test_el_patio_se_puede_filtrar(self, sesion):
        _dentro("ABC1234")
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert 'id="patio"' in contenido
        assert 'data-placa="ABC1234"' in contenido
        assert 'data-filtra="#patio"' in contenido

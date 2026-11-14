"""
Pantallas de la pensión diaria y su presencia en la caja.

Lo que se fija: permisos (cualquier operador da de alta y cobra; solo el perfil
administrativo cambia el precio, da de baja y cancela, con contraseña), la
búsqueda sin distinguir mayúsculas, el recorrido completo de alta a recibo, y
que la caja reconozca al pensionado por su placa.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja, services_pensiones as sp
from apps.estacionamiento.models import (
    EstadoFolio,
    EstadoPension,
    Folio,
    PagoPension,
    Pension,
    PlanPension,
    Tarifa,
    TipoVehiculo,
)

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture(autouse=True)
def tarifa():
    return Tarifa.objects.create(
        nombre="Auto",
        tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajero():
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def duena():
    return get_user_model().objects.create_user(username="duena", password=CLAVE, is_staff=True)


@pytest.fixture
def sesion(client, cajero):
    client.force_login(cajero)
    return client


@pytest.fixture
def turno(cajero):
    return services_caja.abrir_turno(operador=cajero, fondo_inicial=Decimal("0"))


def _alta(operador, placa="ABC123"):
    return sp.registrar_pension(
        placa=placa, tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        nombre_cliente="Sara Ibarra", telefono="5512345678", operador=operador,
    )


class TestPermisos:
    @pytest.mark.parametrize("ruta", ["web:pensiones", "web:pension_nueva"])
    def test_exigen_sesion(self, client, ruta):
        respuesta = client.get(reverse(ruta))
        assert respuesta.status_code == 302
        assert "acceso" in respuesta["Location"] or "login" in respuesta["Location"]

    def test_el_operador_no_cambia_el_precio(self, sesion):
        respuesta = sesion.post(
            reverse("web:pension_precio"), {"precio_diario": "1.00", "clave": CLAVE}
        )
        assert respuesta.status_code == 302
        assert PlanPension.objects.get(activo=True).precio_diario == Decimal("180.00")

    def test_el_operador_no_da_de_baja(self, sesion, cajero):
        pension = _alta(cajero)
        sesion.post(reverse("web:pension_baja", args=[pension.pk]), {"motivo": "x", "clave": CLAVE})
        pension.refresh_from_db()
        assert pension.estado == EstadoPension.ACTIVA

    def test_la_baja_exige_la_contrasena(self, client, duena, cajero):
        pension = _alta(cajero)
        client.force_login(duena)
        client.post(reverse("web:pension_baja", args=[pension.pk]), {"motivo": "Se fue", "clave": "mal"})
        pension.refresh_from_db()
        assert pension.estado == EstadoPension.ACTIVA

        client.post(reverse("web:pension_baja", args=[pension.pk]), {"motivo": "Se fue", "clave": CLAVE})
        pension.refresh_from_db()
        assert pension.estado == EstadoPension.BAJA

    def test_la_duena_cambia_el_precio_con_contrasena(self, client, duena):
        client.force_login(duena)
        client.post(reverse("web:pension_precio"), {"precio_diario": "200.00", "clave": CLAVE})
        assert PlanPension.objects.get(activo=True).precio_diario == Decimal("200.00")

    def test_el_precio_se_ve_en_la_pantalla_de_precio(self, sesion):
        contenido = sesion.get(reverse("web:tarifa")).content.decode()
        assert "Pensión diaria" in contenido
        assert "$180.00" in contenido


class TestRecorrido:
    def test_alta_cobro_y_recibo(self, sesion, turno):
        respuesta = sesion.post(
            reverse("web:pension_nueva"),
            {
                "placa": "vgh 3120", "tipo_vehiculo": "AUTO",
                "nombre_cliente": "Daniela Ortiz", "telefono": "55 5550 0142",
                "accion": "cobrar",
            },
        )
        pension = Pension.objects.get(placa="VGH3120")
        assert respuesta["Location"] == reverse("web:pension_cobrar", args=[pension.pk])

        contenido = sesion.get(respuesta["Location"] + "?dias=2").content.decode()
        assert "$360.00" in contenido

        respuesta = sesion.post(
            reverse("web:pension_cobrar", args=[pension.pk]),
            {"dias": "2", "pago_recibido": "400.00"},
        )
        pago = PagoPension.objects.get()
        assert respuesta["Location"] == reverse("web:pension_recibo", args=[pago.uuid])
        assert pago.cambio == Decimal("40.00")

        recibo = sesion.get(respuesta["Location"]).content.decode()
        assert "VGH3120" in recibo
        assert "$360.00" in recibo

    def test_sin_turno_la_pantalla_lo_dice_y_no_cobra(self, sesion, cajero):
        pension = _alta(cajero)
        contenido = sesion.get(reverse("web:pension_cobrar", args=[pension.pk])).content.decode()
        assert "No tiene un turno de caja abierto" in contenido
        sesion.post(reverse("web:pension_cobrar", args=[pension.pk]), {"dias": "1"})
        assert not PagoPension.objects.exists()

    def test_doble_envio_del_alta_no_duplica(self, sesion):
        datos = {"placa": "ABC123", "tipo_vehiculo": "AUTO", "nombre_cliente": "Ana", "accion": "guardar"}
        sesion.post(reverse("web:pension_nueva"), datos)
        respuesta = sesion.post(reverse("web:pension_nueva"), datos)
        assert respuesta.status_code == 400
        assert "ya tiene una pensión activa" in respuesta.content.decode()
        assert Pension.objects.count() == 1

    def test_la_lista_busca_sin_distinguir_mayusculas(self, sesion, cajero):
        _alta(cajero)
        _alta(cajero, placa="XYZ987")
        for texto in ("abc", "SARA", "1234"):
            contenido = sesion.get(reverse("web:pensiones"), {"q": texto}).content.decode()
            assert "ABC123" in contenido, texto
        contenido = sesion.get(reverse("web:pensiones"), {"q": "abc"}).content.decode()
        assert "XYZ987" not in contenido


class TestEnLaCaja:
    def _vigente(self, operador, horas=10):
        pension = _alta(operador)
        Pension.objects.filter(pk=pension.pk).update(
            vigente_hasta=timezone.now() + timedelta(hours=horas)
        )
        return pension

    def test_entrada_del_pensionado_vuelve_a_la_caja_sin_boleto(self, sesion, cajero):
        self._vigente(cajero)
        respuesta = sesion.post(reverse("web:caja"), {"accion": "entrada", "placa": "abc123"})
        assert respuesta["Location"] == reverse("web:caja")
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert "Entrada de pensionado ABC123" in contenido
        assert "pildora pension" in contenido

    def test_entrada_con_pension_vencida_avisa_y_emite_boleto(self, sesion, cajero):
        pension = _alta(cajero)
        Pension.objects.filter(pk=pension.pk).update(
            vigente_hasta=timezone.now() - timedelta(hours=1)
        )
        respuesta = sesion.post(reverse("web:caja"), {"accion": "entrada", "placa": "ABC123"})
        assert "/boleto/" in respuesta["Location"]
        contenido = sesion.get(respuesta["Location"]).content.decode()
        assert "se cobra por hora, salvo que renueve" in contenido

    def test_salida_cubierta_sin_campo_de_pago_y_sin_turno(self, sesion, cajero):
        self._vigente(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        contenido = sesion.post(
            reverse("web:caja"), {"accion": "cotizar", "placa": "ABC123"}
        ).content.decode()
        assert "Estancia cubierta por pensión" in contenido
        assert 'name="pago_recibido"' not in contenido

        respuesta = sesion.post(reverse("web:caja"), {"accion": "cobrar", "folio": str(folio.uuid)})
        assert respuesta["Location"] == reverse("web:caja")
        folio.refresh_from_db()
        assert folio.estado == EstadoFolio.CERRADO
        assert folio.turno is None
        assert "Sin cobro" in sesion.get(reverse("web:caja")).content.decode()

    def test_salida_cubierta_no_la_frena_un_turno_de_ayer(self, sesion, cajero):
        from apps.estacionamiento.models import Turno

        Turno.objects.create(operador=cajero, abierto_en=timezone.now() - timedelta(days=2))
        self._vigente(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        respuesta = sesion.post(reverse("web:caja"), {"accion": "cobrar", "folio": str(folio.uuid)})
        assert respuesta["Location"] == reverse("web:caja")
        folio.refresh_from_db()
        assert folio.estado == EstadoFolio.CERRADO

    def test_excedente_ofrece_renovar(self, sesion, cajero, turno):
        pension = _alta(cajero)
        ahora = timezone.now()
        Pension.objects.filter(pk=pension.pk).update(vigente_hasta=ahora - timedelta(minutes=60))
        Folio.objects.create(
            placa="ABC123", pension=pension, entrada_en=ahora - timedelta(hours=3),
            tarifa=Tarifa.objects.get(activa=True, tipo_vehiculo=TipoVehiculo.AUTOMOVIL),
        )
        contenido = sesion.post(
            reverse("web:caja"), {"accion": "cotizar", "placa": "ABC123"}
        ).content.decode()
        assert "Pensión vencida" in contenido
        assert "$30.00" in contenido  # una hora de excedente, sin cortesía
        assert reverse("web:pension_cobrar", args=[pension.pk]) in contenido

    def test_la_navegacion_lleva_a_pensiones(self, sesion):
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert reverse("web:pensiones") in contenido

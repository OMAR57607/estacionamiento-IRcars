"""
Pruebas de la pantalla de turno y corte.

Lo que se fija aquí es el control, no la estética: quién puede ver cuánto
debería haber en caja **antes** de contarla. Si esa cifra se le escapa al
cajero, el conteo se ajusta a ella y un faltante no vuelve a registrarse
jamás, sin que nada parezca roto.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja
from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def escenario():
    """Precio vigente, un cajón y un cobro hecho en el turno abierto."""
    Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=0,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=1),
    )
    Cajon.objects.create(codigo="A-01", tipo=TipoCajon.AUTOMOVIL)
    return None


@pytest.fixture
def cajero(escenario):
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def duena(escenario):
    return get_user_model().objects.create_user(
        username="duena", password=CLAVE, is_staff=True
    )


def _turno_con_un_cobro(operador):
    services_caja.abrir_turno(operador=operador, fondo_inicial=Decimal("500.00"))
    folio = services.registrar_entrada(
        placa="ABC1234",
        operador=operador,
        momento=timezone.now() - timedelta(hours=2),
    )
    services.registrar_salida(
        uuid_folio=folio.uuid, pago_recibido=Decimal("100.00"), operador=operador
    )


class TestCierreCiego:
    def test_el_cajero_no_ve_cuanto_deberia_haber(self, client, cajero):
        _turno_con_un_cobro(cajero)
        client.force_login(cajero)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Debería haber en caja" not in contenido
        assert "$560.00" not in contenido, (
            "Ni la cifra ni su rótulo: con el total a la vista el conteo se "
            "ajusta a él y el faltante no se registra"
        )
        assert "$60.00" not in contenido, "Tampoco lo cobrado, que lo delata"

    def test_el_cajero_sí_ve_el_avance_operativo(self, client, cajero):
        """
        Ocultar importes no es ocultarlo todo: cuántos boletos cobró y
        cuántos coches quedan dentro es lo que necesita para trabajar.
        """
        _turno_con_un_cobro(cajero)
        client.force_login(cajero)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Boletos cobrados" in contenido
        assert "Vehículos dentro" in contenido

    def test_el_perfil_administrativo_sí_ve_el_esperado(self, client, duena):
        """
        A la dueña ocultárselo no controla a nadie: puede consultarlo en
        Reportes. Y se lo impide detectar un desvío mientras hay tiempo.
        """
        _turno_con_un_cobro(duena)
        client.force_login(duena)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Debería haber en caja" in contenido
        assert "$560.00" in contenido  # 500 de cambio + 60 cobrados

    def test_la_explicacion_del_cierre_ciego_solo_se_le_da_a_quien_la_padece(
        self, client, cajero, duena
    ):
        _turno_con_un_cobro(cajero)

        client.force_login(cajero)
        del_cajero = client.get(reverse("web:turno")).content.decode()
        assert "¿Por qué no se muestra cuánto debería haber?" in del_cajero

        client.force_login(duena)
        de_la_duena = client.get(reverse("web:turno")).content.decode()
        assert "¿Por qué no se muestra cuánto debería haber?" not in de_la_duena


class TestVocabulario:
    def test_la_pantalla_habla_como_la_caseta(self, client, cajero):
        """
        «Folio» y «fondo inicial» son palabras de contador. En la caseta se
        dice boleto y cambio, y la misma cosa con dos nombres distintos en
        dos pantallas es lo que hace que una tarifa se capture mal.
        """
        _turno_con_un_cobro(cajero)
        client.force_login(cajero)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Cambio con que abrió" in contenido
        assert "Boletos cobrados" in contenido
        assert "Folios cobrados" not in contenido
        assert "Fondo inicial" not in contenido

    def test_la_pantalla_explica_el_flujo_del_turno(self, client, cajero):
        _turno_con_un_cobro(cajero)
        client.force_login(cajero)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "¿Cómo funciona el turno?" in contenido
        assert "se cobran en el turno siguiente" in contenido

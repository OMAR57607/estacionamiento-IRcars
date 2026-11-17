"""
Pruebas de la pantalla de turno y corte.

Lo que se fija aquí es el control, no la estética: quién puede ver cuánto
debería haber en caja **antes** de contarla. Si esa cifra se le escapa al
cajero, el conteo se ajusta a ella y un faltante no vuelve a registrarse
jamás, sin que nada parezca roto.
"""
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja
from apps.estacionamiento.models import Cajon, EstadoTurno, Tarifa, TipoCajon, Turno

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


def _texto_visible(html: str) -> str:
    """
    El texto que el cajero lee, sin marcado, atributos, estilos ni scripts.

    Lo bastante fiel para buscar una palabra; no es un intérprete de HTML.
    """
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


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


class TestConfirmacionDelCierre:
    """
    El cierre es definitivo, y un error de dedo —9825 por 982.50— producía un
    faltante falso que solo se arregla con una corrección administrativa. Por
    eso el conteo se confirma antes de cerrar.
    """

    @staticmethod
    def _turno_abierto(operador):
        _turno_con_un_cobro(operador)  # 500 de cambio + 60 cobrados
        return services.obtener_turno_abierto(operador)

    def test_un_solo_envío_no_cierra_el_turno(self, client, cajero):
        turno = self._turno_abierto(cajero)
        client.force_login(cajero)

        respuesta = client.post(
            reverse("web:turno"), {"accion": "cerrar", "efectivo_declarado": "9825"}
        )

        turno.refresh_from_db()
        assert respuesta.status_code == 200
        assert turno.estado == EstadoTurno.ABIERTO
        contenido = respuesta.content.decode()
        assert "Confirme su conteo" in contenido
        assert "$9,825.00" in contenido

    @pytest.mark.parametrize("perfil", ["cajero", "duena"])
    def test_la_confirmación_no_revela_lo_que_debería_haber(
        self, client, cajero, duena, perfil
    ):
        """
        Ni al cajero ni al dueño: con el esperado a la vista, «Corregir la
        cifra» serviría para ajustar el conteo hasta que cuadre.
        """
        usuario = {"cajero": cajero, "duena": duena}[perfil]
        self._turno_abierto(usuario)
        client.force_login(usuario)

        contenido = client.post(
            reverse("web:turno"), {"accion": "cerrar", "efectivo_declarado": "999"}
        ).content.decode()

        assert "$999.00" in contenido
        for revelador in ("$560.00", "$60.00", "Debería haber", "Faltante",
                          "FALTANTE", "Sobrante", "SOBRANTE", "Diferencia"):
            assert revelador not in contenido, revelador

    def test_confirmar_cierra_y_lleva_al_comprobante(self, client, cajero):
        turno = self._turno_abierto(cajero)
        client.force_login(cajero)

        respuesta = client.post(
            reverse("web:turno"),
            {
                "accion": "confirmar",
                "turno": str(turno.pk),
                "efectivo_declarado": "560.00",
                "observaciones": "Sin novedad",
            },
        )

        turno.refresh_from_db()
        assert respuesta.status_code == 302
        assert respuesta.url == reverse("web:corte", kwargs={"turno_id": turno.pk})
        assert turno.estado == EstadoTurno.CERRADO
        assert turno.efectivo_declarado == Decimal("560.00")
        assert "Sin novedad" in turno.observaciones

    def test_corregir_vuelve_al_formulario_con_lo_capturado(self, client, cajero):
        turno = self._turno_abierto(cajero)
        client.force_login(cajero)

        contenido = client.post(
            reverse("web:turno"),
            {
                "accion": "corregir",
                "turno": str(turno.pk),
                "efectivo_declarado": "9825",
                "observaciones": "Billete roto",
            },
        ).content.decode()

        turno.refresh_from_db()
        assert turno.estado == EstadoTurno.ABIERTO
        assert 'value="9825"' in contenido
        assert 'value="Billete roto"' in contenido

    def test_una_confirmación_de_otro_turno_no_se_aplica(self, client, cajero):
        """
        Si entre los dos pasos el turno se cerró y se abrió otro desde otra
        pestaña, la cifra confirmada era la de otra caja.
        """
        turno = self._turno_abierto(cajero)
        client.force_login(cajero)

        client.post(
            reverse("web:turno"),
            {"accion": "confirmar", "turno": str(turno.pk + 999),
             "efectivo_declarado": "560.00"},
        )

        turno.refresh_from_db()
        assert turno.estado == EstadoTurno.ABIERTO

    def test_un_doble_toque_lleva_al_comprobante_ya_generado(self, client, cajero):
        turno = self._turno_abierto(cajero)
        client.force_login(cajero)
        datos = {"accion": "confirmar", "turno": str(turno.pk),
                 "efectivo_declarado": "560.00"}

        client.post(reverse("web:turno"), datos)
        segunda = client.post(reverse("web:turno"), datos)

        assert segunda.status_code == 302
        assert segunda.url == reverse("web:corte", kwargs={"turno_id": turno.pk})
        assert Turno.objects.filter(operador=cajero).count() == 1

    def test_una_cifra_alterada_en_la_confirmación_se_rechaza(self, client, cajero):
        """La cifra viaja en un campo oculto: el servidor la valida otra vez."""
        turno = self._turno_abierto(cajero)
        client.force_login(cajero)

        client.post(
            reverse("web:turno"),
            {"accion": "confirmar", "turno": str(turno.pk),
             "efectivo_declarado": "-50"},
        )

        turno.refresh_from_db()
        assert turno.estado == EstadoTurno.ABIERTO


class TestTurnosDeOtros:
    """
    El dueño entraba a «Turno y corte» y veía «Abrir turno de caja» mientras
    su cajero llevaba horas cobrando: la pantalla solo resolvía el turno del
    propio usuario.
    """

    def test_el_perfil_administrativo_ve_la_caja_de_otro_operador(
        self, client, cajero, duena
    ):
        _turno_con_un_cobro(cajero)
        client.force_login(duena)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Turnos abiertos de otros operadores (1)" in contenido
        assert "cajero" in contenido
        assert "Debería haber en caja" in contenido
        assert "$560.00" in contenido  # 500 de cambio + 60 cobrados

    def test_el_cajero_no_ve_la_caja_de_un_compañero(self, client, cajero, duena):
        """
        El esperado de un compañero es una referencia para ajustar el propio
        conteo: el cierre ciego no admite ninguna cifra del turno abierto,
        sea de quien sea.
        """
        _turno_con_un_cobro(duena)
        otro = get_user_model().objects.create_user(username="otro", password=CLAVE)
        services_caja.abrir_turno(operador=otro, fondo_inicial=Decimal("200.00"))
        client.force_login(otro)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Turnos abiertos de otros operadores" not in contenido
        assert "Debería haber en caja" not in contenido
        assert "$560.00" not in contenido
        assert "$60.00" not in contenido

    def test_el_turno_propio_no_se_repite_en_la_lista(self, client, cajero, duena):
        _turno_con_un_cobro(duena)
        client.force_login(duena)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Turnos abiertos de otros operadores" not in contenido
        # Su propia cuenta sigue arriba, una sola vez.
        assert contenido.count("Debería haber en caja") == 1

    def test_avisa_del_turno_de_otro_que_viene_de_otra_jornada(
        self, client, cajero, duena
    ):
        services_caja.abrir_turno(
            operador=cajero, momento=timezone.now() - timedelta(days=1, hours=1)
        )
        client.force_login(duena)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "Viene de otra jornada" in contenido


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

    @pytest.mark.parametrize(
        "pantalla",
        ["caja", "cobro", "cancelar", "operadores", "operador", "mantenimiento"],
    )
    def test_ninguna_pantalla_dice_folio(self, client, duena, pantalla):
        """
        La misma regla en todas las pantallas de la caseta, no solo en la del
        turno: en la caja el botón decía «Cobrar y cerrar folio» junto a un
        «Boleto» en la lista del patio, y la pantalla de cancelación hablaba
        de cancelar un folio que el cajero acababa de imprimir como boleto.

        Se revisa el texto que se lee, no el HTML: «folio» sigue siendo el
        nombre del campo oculto y de las rutas, y cambiarlo rompería la API y
        los formularios sin que el cajero ganara nada.
        """
        services_caja.abrir_turno(operador=duena)
        folio = services.registrar_entrada(placa="ABC1234", operador=duena)
        client.force_login(duena)

        if pantalla == "cobro":
            respuesta = client.post(
                reverse("web:caja"), {"accion": "cotizar", "placa": "ABC1234"}
            )
        else:
            rutas = {
                "caja": reverse("web:caja"),
                "cancelar": reverse("web:cancelar", args=[folio.uuid]),
                "operadores": reverse("web:operadores"),
                "operador": reverse("web:operador", args=[duena.pk]),
                "mantenimiento": reverse("web:mantenimiento"),
            }
            respuesta = client.get(rutas[pantalla])

        assert respuesta.status_code == 200
        texto = _texto_visible(respuesta.content.decode())
        assert "folio" not in texto.lower(), (
            f"«folio» en el texto visible de {pantalla}: "
            f"…{texto[max(0, texto.lower().find('folio') - 60):][:120]}…"
        )

    def test_la_pantalla_explica_el_flujo_del_turno(self, client, cajero):
        _turno_con_un_cobro(cajero)
        client.force_login(cajero)

        contenido = client.get(reverse("web:turno")).content.decode()

        assert "¿Cómo funciona el turno?" in contenido
        assert "se cobran en el turno siguiente" in contenido

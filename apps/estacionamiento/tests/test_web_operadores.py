"""
Pruebas de la pantalla de operadores.

Sustituye al panel de administración de Django, que no se monta. Eso cambia lo
que hay que probar: **no hay red de seguridad**. Si el último administrador se
quita los permisos o se desactiva, nadie puede devolvérselos y la instalación
queda sin gobierno, con los datos dentro y sin forma de entrar.

Por eso la mayor parte de estas pruebas comprueba lo que la pantalla **impide**,
no lo que permite.
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
CLAVE_NUEVA = "Clave-Nueva-Valida-2026"


@pytest.fixture(autouse=True)
def estaticos_sin_manifiesto(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture
def jefe():
    return get_user_model().objects.create_user(
        username="omar", password=CLAVE, is_staff=True
    )


@pytest.fixture
def ayudante():
    return get_user_model().objects.create_user(username="ayudante", password=CLAVE)


@pytest.fixture
def sesion(client, jefe):
    client.force_login(jefe)
    return client


def ficha(usuario) -> str:
    return reverse("web:operador", kwargs={"pk": usuario.pk})


class TestAcceso:
    def test_solo_los_administrativos_entran(self, client, ayudante):
        """
        Quien puede crear cuentas puede crear una cuenta que cobre. Tiene el
        mismo peso que cambiar el precio, y se restringe igual.
        """
        client.force_login(ayudante)
        respuesta = client.get(reverse("web:operadores"), secure=True)

        assert respuesta.status_code == 302
        assert reverse("login") in respuesta["Location"]

    def test_exige_sesion(self, client):
        respuesta = client.get(reverse("web:operadores"), secure=True)
        assert respuesta.status_code == 302


class TestAlta:
    def test_crea_un_operador_de_cobro(self, sesion):
        sesion.post(
            reverse("web:operadores"),
            {"usuario": "nuevo", "clave": CLAVE_NUEVA, "clave_repetida": CLAVE_NUEVA},
            secure=True,
        )

        creado = get_user_model().objects.get(username="nuevo")
        assert creado.check_password(CLAVE_NUEVA)
        assert not creado.is_staff, "Sin marcar, no debe poder cambiar el precio"

    def test_crea_un_operador_administrativo(self, sesion):
        sesion.post(
            reverse("web:operadores"),
            {
                "usuario": "socio",
                "clave": CLAVE_NUEVA,
                "clave_repetida": CLAVE_NUEVA,
                "administrativo": "on",
            },
            secure=True,
        )
        assert get_user_model().objects.get(username="socio").is_staff

    def test_las_contrasenas_deben_coincidir(self, sesion):
        sesion.post(
            reverse("web:operadores"),
            {"usuario": "nuevo", "clave": CLAVE_NUEVA, "clave_repetida": "otra-cosa-9"},
            secure=True,
        )
        assert not get_user_model().objects.filter(username="nuevo").exists()

    def test_aplica_la_politica_de_contrasenas(self, sesion):
        sesion.post(
            reverse("web:operadores"),
            {"usuario": "nuevo", "clave": "1234", "clave_repetida": "1234"},
            secure=True,
        )
        assert not get_user_model().objects.filter(username="nuevo").exists()

    def test_rechaza_un_nombre_que_solo_difiere_en_mayusculas(self, sesion, jefe):
        """
        Sostiene el acceso insensible a mayúsculas.

        Con «omar» y «Omar» a la vez, entrar se vuelve ambiguo y, por diseño,
        no se entra a ninguna. Impedir el choque aquí es lo que permite que el
        teclado de un teléfono no deje fuera al operador.
        """
        sesion.post(
            reverse("web:operadores"),
            {"usuario": "OMAR", "clave": CLAVE_NUEVA, "clave_repetida": CLAVE_NUEVA},
            secure=True,
        )
        assert get_user_model().objects.filter(username__iexact="omar").count() == 1


class TestCambioDeNombre:
    def test_renombra_conservando_el_historial(self, sesion, jefe, ayudante):
        """
        Responde a la pregunta original: cómo cambiar el nombre de usuario.

        El historial no se ve afectado porque folios y turnos se guardan
        contra la persona, no contra el texto del nombre.
        """
        tarifa = Tarifa.objects.create(
            nombre="General",
            cuota_hora=Decimal("30.00"),
            vigente_desde=timezone.now() - timedelta(days=1),
        )
        Cajon.objects.create(codigo="A-01", tipo=TipoCajon.AUTOMOVIL)
        folio = services.registrar_entrada(placa="ABC1234", operador=ayudante)

        sesion.post(
            ficha(ayudante),
            {"accion": "datos", "usuario": "jose.luis"},
            secure=True,
        )

        ayudante.refresh_from_db()
        folio.refresh_from_db()
        assert ayudante.username == "jose.luis"
        assert folio.operador_entrada_id == ayudante.pk
        assert tarifa.pk is not None

    def test_rechaza_renombrar_a_un_nombre_ya_usado(self, sesion, jefe, ayudante):
        sesion.post(
            ficha(ayudante), {"accion": "datos", "usuario": "Omar"}, secure=True
        )

        ayudante.refresh_from_db()
        assert ayudante.username == "ayudante"


class TestSalvaguardasDeGobierno:
    """
    Lo esencial de esta pantalla. Sin panel de administración, un error aquí
    no se puede deshacer desde ninguna parte.
    """

    def test_el_ultimo_administrativo_no_puede_quitarse_el_permiso(self, sesion, jefe):
        sesion.post(
            ficha(jefe), {"accion": "datos", "usuario": "omar"}, secure=True
        )

        jefe.refresh_from_db()
        assert jefe.is_staff, "Nadie podría volver a conceder el permiso"

    def test_puede_cederlo_cuando_ya_hay_otro_administrativo(self, sesion, jefe):
        otro = get_user_model().objects.create_user(
            username="socio", password=CLAVE, is_staff=True
        )

        sesion.post(ficha(jefe), {"accion": "datos", "usuario": "omar"}, secure=True)

        jefe.refresh_from_db()
        assert not jefe.is_staff
        assert otro.is_staff

    def test_nadie_puede_desactivarse_a_si_mismo(self, sesion, jefe):
        sesion.post(
            ficha(jefe), {"accion": "estado", "activo": "0"}, secure=True
        )

        jefe.refresh_from_db()
        assert jefe.is_active, "Perdería el acceso en el acto"

    def test_desactivar_a_otro_administrativo_siempre_deja_uno_en_pie(
        self, sesion, jefe
    ):
        """
        Documenta un invariante estructural, no una comprobación del código.

        Desactivar a **otro** administrativo nunca puede dejar la instalación
        sin gobierno: quien ejecuta la acción es forzosamente un
        administrativo activo —lo exige el decorador— y es distinto del
        objetivo, porque desactivarse a uno mismo se rechaza antes. Siempre
        queda al menos uno en pie.

        Esta prueba existe porque una versión anterior añadía aquí una
        comprobación «por si acaso» que nunca podía cumplirse, con una prueba
        que pasaba por un motivo equivocado: el actor se había quedado sin
        permisos y la petición ni siquiera llegaba a la vista. El código
        muerto que aparenta proteger algo es peor que su ausencia.
        """
        otro_jefe = get_user_model().objects.create_user(
            username="socio", password=CLAVE, is_staff=True
        )

        sesion.post(ficha(otro_jefe), {"accion": "estado", "activo": "0"}, secure=True)

        otro_jefe.refresh_from_db()
        jefe.refresh_from_db()
        assert not otro_jefe.is_active, "Debe poder desactivarse"
        assert jefe.is_staff and jefe.is_active, "Y queda un administrativo activo"

        quedan = get_user_model().objects.filter(is_staff=True, is_active=True)
        assert quedan.count() >= 1

    def test_no_se_desactiva_a_quien_tiene_turno_abierto(self, sesion, jefe, ayudante):
        """
        Desactivarlo dejaría ese efectivo sin corte y sin responsable.
        """
        services_caja.abrir_turno(operador=ayudante, fondo_inicial=Decimal("500.00"))

        sesion.post(ficha(ayudante), {"accion": "estado", "activo": "0"}, secure=True)

        ayudante.refresh_from_db()
        assert ayudante.is_active

    def test_se_desactiva_a_quien_no_tiene_turno(self, sesion, jefe, ayudante):
        sesion.post(ficha(ayudante), {"accion": "estado", "activo": "0"}, secure=True)

        ayudante.refresh_from_db()
        assert not ayudante.is_active

    def test_un_operador_desactivado_no_entra(self, client, ayudante):
        ayudante.is_active = False
        ayudante.save(update_fields=["is_active"])

        respuesta = client.post(
            reverse("login"),
            {"username": "ayudante", "password": CLAVE},
            secure=True,
        )
        assert respuesta.status_code == 200

    def test_se_puede_reactivar(self, sesion, jefe, ayudante):
        ayudante.is_active = False
        ayudante.save(update_fields=["is_active"])

        sesion.post(ficha(ayudante), {"accion": "estado", "activo": "1"}, secure=True)

        ayudante.refresh_from_db()
        assert ayudante.is_active


class TestRestablecerContrasena:
    def test_restablece_la_contrasena_de_otro(self, sesion, jefe, ayudante):
        sesion.post(
            ficha(ayudante),
            {"accion": "clave", "clave": CLAVE_NUEVA, "clave_repetida": CLAVE_NUEVA},
            secure=True,
        )

        ayudante.refresh_from_db()
        assert ayudante.check_password(CLAVE_NUEVA)

    def test_aplica_la_politica(self, sesion, jefe, ayudante):
        sesion.post(
            ficha(ayudante),
            {"accion": "clave", "clave": "1234", "clave_repetida": "1234"},
            secure=True,
        )

        ayudante.refresh_from_db()
        assert ayudante.check_password(CLAVE)


class TestBorrado:
    def test_no_existe_forma_de_borrar_un_operador(self, sesion, ayudante):
        """
        No es una omisión. Folios y turnos referencian al operador con
        `PROTECT`: borrar a quien tiene historial rompería la trazabilidad de
        movimientos con efecto económico. Lo que corresponde es desactivar.
        """
        contenido = sesion.get(ficha(ayudante), secure=True).content.decode()

        assert "Borrar" not in contenido
        assert "Eliminar" not in contenido
        assert "Desactivar acceso" in contenido

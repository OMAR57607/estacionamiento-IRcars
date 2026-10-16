"""
Pruebas del alta del operador inicial.

Es la única vía de crear el primer usuario: sin panel de administración y sin
terminal en el contenedor, si este comando falla nadie puede entrar a la caja.
Y como se ejecuta en cada arranque, lo que más importa es lo que **no** hace.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from io import StringIO

pytestmark = pytest.mark.django_db

CLAVE_BUENA = "Cajero-IRcars-2026"


@pytest.fixture
def entorno(monkeypatch):
    def _definir(usuario="cajero", clave=CLAVE_BUENA):
        if usuario is None:
            monkeypatch.delenv("IRCARS_USUARIO_OPERADOR", raising=False)
        else:
            monkeypatch.setenv("IRCARS_USUARIO_OPERADOR", usuario)
        if clave is None:
            monkeypatch.delenv("IRCARS_CLAVE_OPERADOR", raising=False)
        else:
            monkeypatch.setenv("IRCARS_CLAVE_OPERADOR", clave)

    return _definir


class TestAlta:
    def test_crea_al_operador(self, entorno):
        entorno()
        call_command("crear_operador", stdout=StringIO())

        usuario = get_user_model().objects.get(username="cajero")
        assert usuario.check_password(CLAVE_BUENA)

    def test_el_primer_usuario_puede_fijar_el_precio(self, entorno):
        """
        Se crea con acceso administrativo porque, si no, el sistema queda
        inservible: nadie podría dar de alta el cuadro tarifario y la caja no
        puede cobrar sin precio vigente.

        `is_staff` abre las pantallas administrativas **de la aplicación**, no
        el panel de Django, que no está montado.
        """
        entorno()
        call_command("crear_operador", stdout=StringIO())

        assert get_user_model().objects.get(username="cajero").is_staff

    def test_puede_crearse_sin_acceso_administrativo(self, entorno):
        """Para los operadores que se añadan después, que solo cobran."""
        entorno(usuario="ayudante")
        call_command("crear_operador", "--operativo", stdout=StringIO())

        assert not get_user_model().objects.get(username="ayudante").is_staff


class TestIdempotencia:
    def test_no_falla_si_el_operador_ya_existe(self, entorno):
        """Se invoca en cada arranque: el caso normal es que ya esté."""
        entorno()
        call_command("crear_operador", stdout=StringIO())
        call_command("crear_operador", stdout=StringIO())

        assert get_user_model().objects.filter(username="cajero").count() == 1

    def test_nunca_restablece_la_contrasena_de_una_cuenta_existente(self, entorno):
        """
        Lo más importante del comando.

        Si restableciera la contraseña en cada arranque, la cuenta quedaría
        atada al valor de una variable: quien la cambiara desde la aplicación
        la vería revertida en el siguiente despliegue, sin explicación alguna.
        """
        entorno()
        call_command("crear_operador", stdout=StringIO())

        usuario = get_user_model().objects.get(username="cajero")
        usuario.set_password("La-Cambie-Yo-Mismo-2026")
        usuario.save()

        entorno(clave="Otra-Clave-Distinta-2026")
        call_command("crear_operador", stdout=StringIO())

        usuario.refresh_from_db()
        assert usuario.check_password("La-Cambie-Yo-Mismo-2026")


class TestSalvaguardas:
    def test_exige_contrasena(self, entorno):
        """
        Una cuenta sin contraseña quedaría inaccesible y habría que borrarla
        a mano, justo en el momento en que no hay panel para hacerlo.
        """
        entorno(clave=None)
        with pytest.raises(CommandError, match="IRCARS_CLAVE_OPERADOR"):
            call_command("crear_operador", stdout=StringIO())

        assert not get_user_model().objects.exists()

    def test_exige_nombre_de_usuario(self, entorno):
        entorno(usuario=None)
        with pytest.raises(CommandError, match="IRCARS_USUARIO_OPERADOR"):
            call_command("crear_operador", stdout=StringIO())

    def test_aplica_la_politica_de_contrasenas(self, entorno):
        """
        Una cuenta creada por arranque no debe saltarse la política que la
        aplicación exige en cualquier otro cambio de contraseña.
        """
        entorno(clave="1234")
        with pytest.raises(CommandError, match="política"):
            call_command("crear_operador", stdout=StringIO())

        assert not get_user_model().objects.exists()


class TestCambioDeContrasena:
    """
    La contraseña con que se crea la cuenta tiene que poder rotarse.

    Sin esta pantalla —y sin panel de administración— la clave del primer
    operador quedaría fijada para siempre al valor de una variable de entorno
    del servidor, conocida por quien hizo la instalación. El comando aconseja
    cambiarla desde la aplicación; estas pruebas comprueban que ese consejo
    sea cierto.
    """

    @pytest.fixture
    def sesion(self, client, entorno):
        from django.urls import reverse

        entorno()
        call_command("crear_operador", stdout=StringIO())
        client.login(username="cajero", password=CLAVE_BUENA)
        return client, reverse("web:clave")

    def test_el_operador_puede_cambiar_su_contrasena(self, sesion):
        cliente, ruta = sesion
        nueva = "Mi-Clave-Propia-2026"

        respuesta = cliente.post(
            ruta,
            {
                "old_password": CLAVE_BUENA,
                "new_password1": nueva,
                "new_password2": nueva,
            },
            secure=True,
        )

        assert respuesta.status_code == 302
        usuario = get_user_model().objects.get(username="cajero")
        assert usuario.check_password(nueva)
        assert not usuario.check_password(CLAVE_BUENA)

    def test_exige_la_contrasena_actual(self, sesion):
        """
        Impide que alguien que encuentre una sesión abierta —una caseta
        compartida, un teléfono sin bloquear— se apropie de la cuenta.
        """
        cliente, ruta = sesion
        respuesta = cliente.post(
            ruta,
            {
                "old_password": "no-es-la-actual",
                "new_password1": "Otra-Clave-Valida-2026",
                "new_password2": "Otra-Clave-Valida-2026",
            },
            secure=True,
        )

        assert respuesta.status_code == 200
        assert get_user_model().objects.get(username="cajero").check_password(
            CLAVE_BUENA
        )

    def test_aplica_la_politica_de_contrasenas(self, sesion):
        cliente, ruta = sesion
        respuesta = cliente.post(
            ruta,
            {"old_password": CLAVE_BUENA, "new_password1": "1234", "new_password2": "1234"},
            secure=True,
        )

        assert respuesta.status_code == 200
        assert get_user_model().objects.get(username="cajero").check_password(
            CLAVE_BUENA
        )

    def test_exige_sesion(self, client):
        from django.urls import reverse

        respuesta = client.get(reverse("web:clave"), secure=True)
        assert respuesta.status_code == 302
        assert reverse("login") in respuesta["Location"]

"""
Pruebas de la superficie expuesta a internet.

Con la caja publicada, lo que **no** está accesible importa tanto como lo que
sí. Estas pruebas fijan tres decisiones que de otro modo se revierten sin que
nadie lo note, porque revertirlas no rompe ninguna funcionalidad visible:

1. La raíz no enseña el formulario de contraseña a quien no es del personal.
2. El panel de administración no se monta salvo que se pida explícitamente.
3. El formulario de acceso tiene límite de intentos.
"""
import importlib

import pytest
from django.contrib.auth import get_user_model
from django.urls import clear_url_caches, reverse

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture(autouse=True)
def estaticos_sin_manifiesto(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture
def cajero():
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


class TestRaiz:
    def test_la_caja_vive_en_la_raiz(self, client, cajero):
        """
        Es la pantalla que el operador abre todo el día.

        No debe exigirle recordar una ruta: el servicio está publicado en su
        propia dirección y esa dirección es la aplicación.
        """
        client.force_login(cajero)
        respuesta = client.get("/", secure=True)

        assert respuesta.status_code == 200
        assert "Vehículos dentro" in respuesta.content.decode()

    def test_quien_llega_sin_sesion_va_al_acceso(self, client):
        respuesta = client.get("/", secure=True)

        assert respuesta.status_code == 302
        assert reverse("login") in respuesta["Location"]


class TestPanelDeAdministracion:
    def test_no_se_monta_por_omision(self, client):
        """
        `/admin/` es la ruta más sondeada de cualquier sitio Django.

        La aplicación no lo necesita: el precio, los turnos, el corte y los
        reportes tienen pantallas propias con las salvaguardas del negocio,
        que el panel se saltaría.
        """
        assert client.get("/admin/", secure=True).status_code == 404

    def test_se_monta_solo_en_la_ruta_indicada(self, client, settings):
        """
        Habilitarlo exige nombrar una ruta, y nunca queda en `/admin/`.

        Se recarga el enrutador porque las rutas se construyen al importar el
        módulo; el accesorio lo deja como estaba al terminar.
        """
        import config.urls

        settings.IRCARS_RUTA_ADMIN = "panel-que-nadie-adivina"
        importlib.reload(config.urls)
        clear_url_caches()
        try:
            # Presente donde se pidió: redirige al acceso del propio panel.
            assert client.get("/panel-que-nadie-adivina/", secure=True).status_code in (
                200,
                302,
            )
            # Y sigue ausente de la ruta previsible.
            assert client.get("/admin/", secure=True).status_code == 404
        finally:
            settings.IRCARS_RUTA_ADMIN = ""
            importlib.reload(config.urls)
            clear_url_caches()


class TestLimiteDeAcceso:
    def test_los_intentos_de_contrasena_se_cortan(self, client, cajero):
        """
        Django no limita los intentos de acceso. Con la caja en internet, el
        formulario es el objetivo más obvio del sistema: detrás hay dinero.
        """
        ruta = reverse("login")
        codigos = [
            client.post(
                ruta, {"username": "cajero", "password": f"intento-{n}"}, secure=True
            ).status_code
            for n in range(12)
        ]

        assert 429 in codigos, "Una lista de contraseñas debería cortarse"

    def test_abrir_la_pantalla_no_consume_intentos(self, client, cajero):
        """
        Solo se cuentan los envíos.

        Si contaran las lecturas, un operador que recargara la pantalla —o un
        teléfono que la precargue— se quedaría fuera de su propia caja.
        """
        ruta = reverse("login")
        codigos = {client.get(ruta, secure=True).status_code for _ in range(30)}

        assert codigos == {200}

    def test_el_operador_legitimo_entra(self, client, cajero):
        """El límite no debe estorbar al uso normal."""
        respuesta = client.post(
            reverse("login"),
            {"username": "cajero", "password": CLAVE},
            secure=True,
        )
        assert respuesta.status_code == 302
        assert respuesta["Location"] == reverse("web:caja")


class TestAccesoDesdeUnTelefono:
    """
    Pruebas del fallo que dejó al operador fuera de su propia caja.

    El teclado de Android capitaliza la primera letra de un campo de texto, de
    modo que quien teclea su usuario envía «Omar» donde la cuenta es «omar».
    Django distingue mayúsculas y el acceso falla con «usuario o contraseña
    incorrectos» —con la contraseña bien puesta—, lo que lleva a creer que se
    perdió el acceso.
    """

    def test_el_campo_de_usuario_no_deja_capitalizar_al_teclado(self, client):
        """
        La primera barrera es el propio campo.

        El campo de la placa ya tomaba esta precaución; el de usuario se había
        quedado sin ella.
        """
        contenido = client.get(reverse("login"), secure=True).content.decode()

        inicio = contenido.index('name="username"')
        fin = contenido.index(">", inicio)
        campo = contenido[contenido.rindex("<input", 0, inicio) : fin]

        assert 'autocapitalize="none"' in campo
        assert 'autocorrect="off"' in campo

    @pytest.mark.parametrize("escrito", ["omar", "Omar", "OMAR", "oMaR"])
    def test_el_usuario_entra_sea_cual_sea_la_caja_de_las_letras(
        self, client, escrito
    ):
        """
        La segunda barrera, porque la primera no cubre todo: el operador puede
        escribirlo con mayúscula por costumbre o recuperarlo de un gestor de
        contraseñas guardado con otra caja.
        """
        get_user_model().objects.create_user(username="omar", password=CLAVE)

        respuesta = client.post(
            reverse("login"), {"username": escrito, "password": CLAVE}, secure=True
        )

        assert respuesta.status_code == 302, f"«{escrito}» debería entrar"
        assert respuesta["Location"] == reverse("web:caja")

    def test_la_contrasena_si_distingue_mayusculas(self, client):
        """
        Aquí no se relaja la autenticación: solo se resuelve a qué cuenta se
        refiere el nombre escrito. La contraseña sigue siendo exacta.
        """
        get_user_model().objects.create_user(username="omar", password=CLAVE)

        respuesta = client.post(
            reverse("login"),
            {"username": "omar", "password": CLAVE.upper()},
            secure=True,
        )

        assert respuesta.status_code == 200, "Una contraseña en otra caja no debe valer"

    def test_con_dos_cuentas_ambiguas_no_se_adivina(self, client):
        """
        Si existieran «omar» y «Omar», elegir una sería peor que fallar: el
        operador entraría en una cuenta que no es la suya y el corte se
        atribuiría a otra persona.
        """
        Usuario = get_user_model()
        Usuario.objects.create_user(username="omar", password=CLAVE)
        Usuario.objects.create_user(username="Omar", password="otra-clave-distinta-99")

        respuesta = client.post(
            reverse("login"), {"username": "OMAR", "password": CLAVE}, secure=True
        )

        assert respuesta.status_code == 200, "Ante la ambigüedad, no se entra"

    def test_un_usuario_inexistente_sigue_fallando(self, client):
        respuesta = client.post(
            reverse("login"), {"username": "nadie", "password": CLAVE}, secure=True
        )
        assert respuesta.status_code == 200

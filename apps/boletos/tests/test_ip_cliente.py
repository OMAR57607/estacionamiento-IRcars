"""
Pruebas de cómo se identifica al solicitante al aplicar límites de tasa.

Importa más de lo que parece. Si la dirección se resuelve mal, el límite no
falla de forma visible: sigue contando, pero cuenta mal. En un extremo deja
pasar un sondeo; en el otro —el que se dio al poner Cloudflare delante—
agrupa a todos los visitantes en un puñado de contadores y puede dejar fuera
al propio operador sin que nadie esté atacando nada.
"""
import pytest
from django.test import RequestFactory
from django.urls import reverse

from apps.boletos.limites import ip_cliente

# Dirección que un borde de Cloudflare presenta al proxy de la plataforma.
# Es la que acabaría en `X-Forwarded-For` y la que agruparía a todos.
BORDE = "172.69.34.10"


@pytest.fixture
def peticion():
    def _construir(**cabeceras):
        return RequestFactory().get("/", **cabeceras)

    return _construir


class TestDetrasDeCloudflare:
    def test_usa_la_direccion_real_del_cliente(self, settings, peticion):
        settings.IRCARS_TRAS_CLOUDFLARE = True
        settings.IRCARS_TRAS_PROXY = True

        solicitud = peticion(
            HTTP_CF_CONNECTING_IP="200.63.47.209",
            HTTP_X_FORWARDED_FOR=f"200.63.47.209, {BORDE}",
            REMOTE_ADDR="10.0.0.1",
        )

        assert ip_cliente(solicitud) == "200.63.47.209"

    def test_no_agrupa_a_dos_clientes_del_mismo_borde(self, settings, peticion):
        """
        Es el fallo concreto que esto corrige.

        Dos personas distintas salen por el mismo borde de Cloudflare: su
        `X-Forwarded-For` termina igual. Con la cabecera de Cloudflare se
        distinguen; sin ella compartirían contador.
        """
        settings.IRCARS_TRAS_CLOUDFLARE = True

        una = peticion(
            HTTP_CF_CONNECTING_IP="200.63.47.209",
            HTTP_X_FORWARDED_FOR=f"200.63.47.209, {BORDE}",
        )
        otra = peticion(
            HTTP_CF_CONNECTING_IP="177.226.138.205",
            HTTP_X_FORWARDED_FOR=f"177.226.138.205, {BORDE}",
        )

        assert ip_cliente(una) != ip_cliente(otra)

    def test_sin_la_cabecera_vuelve_al_proxy_de_la_plataforma(
        self, settings, peticion
    ):
        """
        Su ausencia significa que la petición no pasó por Cloudflare: alguien
        llamó directamente a la dirección de la plataforma. Ahí la entrada
        derecha de `X-Forwarded-For` sí es el cliente real.
        """
        settings.IRCARS_TRAS_CLOUDFLARE = True
        settings.IRCARS_TRAS_PROXY = True

        solicitud = peticion(HTTP_X_FORWARDED_FOR="1.2.3.4, 200.63.47.209")

        assert ip_cliente(solicitud) == "200.63.47.209"


class TestSoloDetrasDelProxy:
    def test_ignora_la_cabecera_de_cloudflare_si_no_se_declara(
        self, settings, peticion
    ):
        """
        Sin estar realmente detrás de Cloudflare, esa cabecera la inventa
        cualquiera: confiar en ella permitiría eludir el límite rotando
        valores.
        """
        settings.IRCARS_TRAS_CLOUDFLARE = False
        settings.IRCARS_TRAS_PROXY = True

        solicitud = peticion(
            HTTP_CF_CONNECTING_IP="9.9.9.9",
            HTTP_X_FORWARDED_FOR="1.2.3.4, 200.63.47.209",
        )

        assert ip_cliente(solicitud) == "200.63.47.209"

    def test_toma_la_entrada_derecha_y_no_la_primera(self, settings, peticion):
        """
        La última entrada es la única que escribió infraestructura propia; las
        anteriores puede ponerlas el cliente.
        """
        settings.IRCARS_TRAS_CLOUDFLARE = False
        settings.IRCARS_TRAS_PROXY = True

        solicitud = peticion(HTTP_X_FORWARDED_FOR="inventada, 200.63.47.209")

        assert ip_cliente(solicitud) == "200.63.47.209"


class TestSinProxy:
    def test_manda_remote_addr(self, settings, peticion):
        settings.IRCARS_TRAS_CLOUDFLARE = False
        settings.IRCARS_TRAS_PROXY = False

        solicitud = peticion(
            HTTP_CF_CONNECTING_IP="9.9.9.9",
            HTTP_X_FORWARDED_FOR="1.2.3.4",
            REMOTE_ADDR="192.168.1.50",
        )

        assert ip_cliente(solicitud) == "192.168.1.50"

    def test_sin_ningun_dato_no_revienta(self, settings, peticion):
        settings.IRCARS_TRAS_CLOUDFLARE = False
        settings.IRCARS_TRAS_PROXY = False

        solicitud = peticion()
        solicitud.META.pop("REMOTE_ADDR", None)

        assert ip_cliente(solicitud) == "desconocida"


@pytest.mark.django_db
class TestEfectoEnElLimiteReal:
    """
    La consecuencia visible: el límite del formulario de acceso.

    Con la resolución rota, los intentos de una persona consumían el cupo de
    todas las que salieran por el mismo borde de Cloudflare.
    """

    def test_dos_clientes_del_mismo_borde_no_comparten_cupo(self, client, settings):
        from django.contrib.auth import get_user_model

        settings.IRCARS_TRAS_CLOUDFLARE = True
        get_user_model().objects.create_user(username="cajero", password="clave-1234")
        ruta = reverse("login")

        # Una persona agota su cupo con intentos fallidos.
        for n in range(12):
            client.post(
                ruta,
                {"username": "cajero", "password": f"mal-{n}"},
                secure=True,
                HTTP_CF_CONNECTING_IP="200.63.47.209",
                HTTP_X_FORWARDED_FOR=f"200.63.47.209, {BORDE}",
            )

        # Otra, detrás del mismo borde, debe poder intentarlo.
        respuesta = client.post(
            ruta,
            {"username": "cajero", "password": "clave-1234"},
            secure=True,
            HTTP_CF_CONNECTING_IP="177.226.138.205",
            HTTP_X_FORWARDED_FOR=f"177.226.138.205, {BORDE}",
        )

        assert respuesta.status_code != 429, (
            "El cupo de una persona no debe agotar el de las demás"
        )

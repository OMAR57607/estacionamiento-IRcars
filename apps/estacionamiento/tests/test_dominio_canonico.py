"""
Pruebas de la redirección al dominio propio.

El motivo de que exista no es cosmético: **los códigos QR ya emitidos llevan
grabado el dominio con el que se generaron**. Un boleto impreso o compartido
por WhatsApp antes de un cambio de dominio seguirá apuntando a la dirección
anterior durante días, y es el cliente —no el operador— quien se queda sin
poder consultarlo.
"""
import uuid as modulo_uuid

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

CANONICO = "ircars.mekanotek.com"
ANTERIOR = "estacionamiento-ircars-production.up.railway.app"


@pytest.fixture
def con_dominio(settings):
    settings.IRCARS_DOMINIO_CANONICO = CANONICO
    settings.ALLOWED_HOSTS = [CANONICO, ANTERIOR, "testserver"]
    return settings


class TestRedireccion:
    def test_lleva_al_dominio_propio(self, client, con_dominio):
        respuesta = client.get("/", secure=True, HTTP_HOST=ANTERIOR)

        assert respuesta.status_code == 302
        assert respuesta["Location"] == f"https://{CANONICO}/"

    def test_conserva_la_ruta_del_boleto(self, client, con_dominio):
        """
        El caso que justifica todo esto.

        Un QR emitido antes del cambio apunta a la dirección anterior. Si la
        redirección perdiera la ruta, el cliente acabaría en la pantalla de
        acceso del personal en lugar de en su boleto.
        """
        identificador = modulo_uuid.uuid4()
        ruta = reverse("boletos:publico", kwargs={"uuid_folio": identificador})

        respuesta = client.get(ruta, secure=True, HTTP_HOST=ANTERIOR)

        assert respuesta["Location"] == f"https://{CANONICO}{ruta}"

    def test_conserva_la_cadena_de_consulta(self, client, con_dominio):
        respuesta = client.get(
            "/reportes/?desde=2026-01-01&hasta=2026-01-31",
            secure=True,
            HTTP_HOST=ANTERIOR,
        )

        assert respuesta["Location"] == (
            f"https://{CANONICO}/reportes/?desde=2026-01-01&hasta=2026-01-31"
        )

    def test_siempre_apunta_a_https(self, client, con_dominio):
        """
        Un salto, no dos.

        Si redirigiera a http, la siguiente respuesta sería otra redirección
        hacia https, y cada salto cuenta en el teléfono de un cliente que está
        escaneando su boleto en la rampa de salida.
        """
        respuesta = client.get("/", HTTP_HOST=ANTERIOR)

        assert respuesta["Location"].startswith("https://")

    def test_no_redirige_si_ya_esta_en_el_dominio_propio(self, client, con_dominio):
        respuesta = client.get("/", secure=True, HTTP_HOST=CANONICO)

        # Redirige al acceso por no haber sesión, no por el dominio.
        assert reverse("login") in respuesta["Location"]
        assert CANONICO not in respuesta["Location"]

    def test_el_puerto_no_cuenta_como_otro_dominio(self, client, con_dominio):
        """En desarrollo el host llega con `:8000` y no es un dominio distinto."""
        con_dominio.ALLOWED_HOSTS = [CANONICO, ANTERIOR, "testserver"]

        respuesta = client.get("/", secure=True, HTTP_HOST=f"{CANONICO}:8000")

        assert reverse("login") in respuesta["Location"]


class TestLoQueNoSeRedirige:
    def test_las_escrituras_pasan_sin_tocar(self, client, con_dominio):
        """
        Lo más importante de este middleware.

        Un POST redirigido es un fallo serio y silencioso: el navegador
        descarta el cuerpo y repite la petición como GET. Aquí significaría
        un cobro que el operador da por hecho y que nunca llegó al servidor.
        """
        respuesta = client.post(
            reverse("login"),
            {"username": "nadie", "password": "x"},
            secure=True,
            HTTP_HOST=ANTERIOR,
        )

        assert respuesta.status_code != 302, "Un POST no debe redirigirse"

    def test_la_sonda_de_salud_no_se_redirige(self, client, con_dominio):
        """
        La plataforma la consulta contra la dirección interna del servicio.

        Redirigirla haría que una comprobación de «¿está vivo el proceso?»
        respondiera 302 y pudiera leerse como caída.
        """
        respuesta = client.get("/healthz", secure=True, HTTP_HOST=ANTERIOR)

        assert respuesta.status_code == 200
        assert respuesta.json() == {"estado": "ok"}

    def test_sin_dominio_configurado_no_redirige_nada(self, client, settings):
        """Desactivada por omisión: una instalación sin dominio propio no se toca."""
        settings.IRCARS_DOMINIO_CANONICO = ""
        settings.ALLOWED_HOSTS = [ANTERIOR, "testserver"]

        respuesta = client.get("/", secure=True, HTTP_HOST=ANTERIOR)

        assert reverse("login") in respuesta["Location"]
        assert ANTERIOR not in respuesta["Location"]

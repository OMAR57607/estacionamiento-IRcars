"""
Pruebas de la pantalla del boleto en la interfaz web.

Es la pantalla que el dependiente pone delante del cliente tras registrar la
entrada, y la que le permite enviarle el enlace. Lo que se verifica aquí es el
recorrido y las dos formas de entrega —código para escanear y enlace para
compartir—, no el cálculo del importe, que está en `test_tarifas.py`.
"""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def tarifa():
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajones():
    return [Cajon.objects.create(codigo="A-01", tipo=TipoCajon.AUTOMOVIL)]


@pytest.fixture
def cajero():
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def sesion(client, cajero):
    client.force_login(cajero)
    return client


@pytest.fixture
def folio(tarifa, cajones, cajero):
    return services.registrar_entrada(placa="ABC1234", operador=cajero)


def ruta_de(folio) -> str:
    return reverse("web:boleto", kwargs={"uuid": folio.uuid})


class TestAcceso:
    def test_exige_sesion(self, client, folio):
        """
        El boleto del operador muestra la placa completa y el patio.

        No es la página pública del cliente: esa es `/t/<uuid>/` y va sin
        credenciales. Esta otra tiene que pedirlas.
        """
        respuesta = client.get(ruta_de(folio))
        assert respuesta.status_code == 302
        assert reverse("login") in respuesta["Location"]

    def test_no_acepta_escrituras(self, sesion, folio):
        """
        Emitir el folio ya ocurrió; esta pantalla solo lo enseña.

        Si aceptara un POST, recargarla podría repetir una operación con
        efecto económico.
        """
        assert sesion.post(ruta_de(folio)).status_code == 405

    def test_un_folio_inexistente_responde_404(self, sesion):
        import uuid as modulo_uuid

        ruta = reverse("web:boleto", kwargs={"uuid": modulo_uuid.uuid4()})
        assert sesion.get(ruta).status_code == 404


class TestContenido:
    def test_dibuja_el_codigo_qr(self, sesion, folio):
        contenido = sesion.get(ruta_de(folio)).content.decode()
        assert "<svg" in contenido
        assert "escanee este código" in contenido.lower()

    def test_muestra_el_enlace_publico_absoluto(self, sesion, folio):
        """
        El enlace tiene que ser absoluto: va a parar a otro teléfono.

        Se construye del propio servidor que atiende, no de una variable de
        configuración, para que un despliegue nuevo no emita códigos que
        apuntan al anterior.
        """
        contenido = sesion.get(ruta_de(folio)).content.decode()
        ruta_publica = reverse("boletos:publico", kwargs={"uuid_folio": folio.uuid})

        assert f"http://testserver{ruta_publica}" in contenido

    def test_ofrece_compartir_por_whatsapp_con_el_enlace(self, sesion, folio):
        contenido = sesion.get(ruta_de(folio)).content.decode()
        assert "wa.me" in contenido

        inicio = contenido.index("https://wa.me/?")
        fin = contenido.index('"', inicio)
        destino = contenido[inicio:fin].replace("&amp;", "&")

        texto = parse_qs(urlparse(destino).query)["text"][0]
        assert str(folio.uuid) in texto

    def test_el_mensaje_de_whatsapp_no_lleva_la_placa(self, sesion, folio):
        """
        El enlace es lo único que el cliente necesita.

        Lo que no se escribe en el mensaje no viaja por WhatsApp, y la placa
        es un dato personal que la propia página del boleto ya enmascara.
        """
        contenido = sesion.get(ruta_de(folio)).content.decode()

        inicio = contenido.index("https://wa.me/?")
        fin = contenido.index('"', inicio)
        destino = contenido[inicio:fin].replace("&amp;", "&")

        texto = parse_qs(urlparse(destino).query)["text"][0]
        assert "ABC1234" not in texto

    def test_el_operador_si_ve_la_placa_completa(self, sesion, folio):
        """
        En esta pantalla sí, porque el operador está autenticado y la necesita
        para confirmar que el boleto corresponde al vehículo que tiene delante.
        El enmascarado protege la página pública, no la del operador.
        """
        contenido = sesion.get(ruta_de(folio)).content.decode()
        assert "ABC1234" in contenido


class TestRecorrido:
    def test_registrar_una_entrada_lleva_al_boleto(self, sesion, tarifa, cajones):
        """
        Es el único momento en que el cliente está delante.

        Volver a la pantalla de caja obligaría al operador a navegar para
        enseñar el código, y en la práctica equivale a que no lo haga.
        """
        respuesta = sesion.post(
            reverse("web:caja"),
            {"accion": "entrada", "placa": "XYZ9876", "tipo_cajon": TipoCajon.AUTOMOVIL},
        )

        assert respuesta.status_code == 302
        assert "/boleto/" in respuesta["Location"]

    def test_el_patio_enlaza_al_boleto_de_cada_vehiculo(self, sesion, folio):
        """
        Para el cliente que perdió el enlace y pide que se lo reenvíen.
        """
        contenido = sesion.get(reverse("web:caja")).content.decode()
        assert ruta_de(folio) in contenido

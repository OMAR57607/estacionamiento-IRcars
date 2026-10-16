"""
Pruebas de la ruta de ingesta: la única superficie de escritura del servicio.

Se verifica el control de acceso antes que el formato de los datos, porque es
el orden en que importa: una ingesta abierta permitiría a cualquiera publicar
boletos falsos con el nombre del estacionamiento.
"""
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.boletos.dominio import RELLENO
from apps.boletos.instantanea import VERSION_CONTRATO, instantanea_publica
from apps.boletos.models import BoletoPublicado

from .conftest import TOKEN_PRUEBA

pytestmark = pytest.mark.django_db


@pytest.fixture
def ruta():
    return reverse("boletos_ingesta:ingesta")


@pytest.fixture
def api():
    return APIClient()


def _enviar(api, ruta, carga, token=TOKEN_PRUEBA):
    """
    Entrega una carga a la ingesta.

    `secure=True` no es un detalle de la prueba: reproduce cómo llega la
    petición en producción. Fuera de depuración la configuración activa
    `SECURE_SSL_REDIRECT`, de modo que una petición por HTTP se contestaría
    con un 301 y nunca alcanzaría la vista. Emitirla cifrada prueba el camino
    real y hace que estas pruebas no dependan del valor de `DJANGO_DEBUG`.
    """
    cabeceras = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return api.post(
        ruta,
        data=json.dumps(carga),
        content_type="application/json",
        secure=True,
        **cabeceras,
    )


@pytest.fixture
def carga(folio_abierto):
    return instantanea_publica(folio_abierto)


class TestControlDeAcceso:
    def test_sin_credencial_se_rechaza(self, api, ruta, carga, servicio_configurado):
        assert _enviar(api, ruta, carga, token=None).status_code == 401
        assert not BoletoPublicado.objects.exists()

    def test_token_incorrecto_se_rechaza(self, api, ruta, carga, servicio_configurado):
        assert _enviar(api, ruta, carga, token="token-equivocado").status_code == 401
        assert not BoletoPublicado.objects.exists()

    def test_token_con_prefijo_correcto_pero_incompleto_se_rechaza(
        self, api, ruta, carga, servicio_configurado
    ):
        """
        Un token que acierta el principio no vale más que uno completamente
        distinto: la comparación es de tiempo constante y no revela el avance.
        """
        parcial = TOKEN_PRUEBA[:-1]
        assert _enviar(api, ruta, carga, token=parcial).status_code == 401

    def test_esquema_distinto_de_bearer_se_rechaza(
        self, api, ruta, carga, servicio_configurado
    ):
        respuesta = api.post(
            ruta,
            data=json.dumps(carga),
            content_type="application/json",
            secure=True,
            HTTP_AUTHORIZATION=f"Token {TOKEN_PRUEBA}",
        )
        assert respuesta.status_code == 401

    def test_servicio_sin_token_configurado_se_cierra(self, api, ruta, carga, settings):
        """
        Sin token configurado la ruta responde 503, no acepta la carga.

        Es la diferencia entre fallar cerrado y fallar abierto. Un despliegue a
        medio configurar que admitiera cualquier escritura sería una puerta
        abierta y nadie lo notaría, porque «funciona».
        """
        settings.IRCARS_TOKEN_PUBLICACION = ""
        assert _enviar(api, ruta, carga, token="lo-que-sea").status_code == 503
        assert not BoletoPublicado.objects.exists()

    def test_la_ruta_no_acepta_lectura(self, api, ruta, servicio_configurado):
        """La ingesta solo escribe; no expone el contenido publicado."""
        assert api.get(ruta, secure=True).status_code == 405


class TestIdempotencia:
    def test_primera_entrega_crea(self, api, ruta, carga, servicio_configurado):
        respuesta = _enviar(api, ruta, carga)
        assert respuesta.status_code == 201
        assert BoletoPublicado.objects.count() == 1

    def test_reenviar_la_misma_carga_no_duplica(
        self, api, ruta, carga, servicio_configurado
    ):
        """
        Reenviar debe ser inofensivo.

        De esta garantía depende todo el publicador: reintenta ante la duda,
        sin llevar registro de lo entregado. Si un reenvío diera 400 por
        duplicado, la bandeja se atascaría para siempre en ese folio.
        """
        assert _enviar(api, ruta, carga).status_code == 201
        segunda = _enviar(api, ruta, carga)
        assert segunda.status_code == 200
        assert BoletoPublicado.objects.count() == 1

    def test_una_entrega_posterior_actualiza_el_estado(
        self, api, ruta, carga, servicio_configurado, folio_abierto, operador
    ):
        from decimal import Decimal

        from apps.estacionamiento import services

        _enviar(api, ruta, carga)

        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        _enviar(api, ruta, instantanea_publica(folio))

        boleto = BoletoPublicado.objects.get(uuid_folio=folio.uuid)
        assert boleto.estado == "CERRADO"
        assert boleto.importe == folio.importe
        assert BoletoPublicado.objects.count() == 1


class TestValidacion:
    def test_rechaza_una_placa_sin_enmascarar(
        self, api, ruta, carga, servicio_configurado
    ):
        """
        Último cerrojo del enmascarado, en el servidor.

        El enmascarado se aplica en la caja, pero el servicio no puede auditar
        el código que corre en la caseta. Rechazar aquí garantiza que la base
        del servicio público nunca contenga una placa íntegra, venga de donde
        venga la carga.
        """
        carga["placa_enmascarada"] = "ABC1234"
        respuesta = _enviar(api, ruta, carga)
        assert respuesta.status_code == 400
        assert not BoletoPublicado.objects.exists()

    def test_acepta_la_placa_enmascarada(self, api, ruta, carga, servicio_configurado):
        assert carga["placa_enmascarada"] == f"ABC{RELLENO * 4}"
        assert _enviar(api, ruta, carga).status_code == 201

    def test_rechaza_una_salida_anterior_a_la_entrada(
        self, api, ruta, carga, servicio_configurado
    ):
        """Un reloj desincronizado no debe producir un boleto incoherente."""
        carga["salida_en"] = (timezone.now() - timedelta(days=2)).isoformat()
        carga["estado"] = "CERRADO"
        assert _enviar(api, ruta, carga).status_code == 400

    def test_rechaza_un_boleto_liquidado_sin_hora_de_salida(
        self, api, ruta, carga, servicio_configurado
    ):
        carga["estado"] = "CERRADO"
        carga["salida_en"] = None
        assert _enviar(api, ruta, carga).status_code == 400

    def test_rechaza_un_estado_desconocido(self, api, ruta, carga, servicio_configurado):
        carga["estado"] = "PAGADO_CON_TARJETA"
        assert _enviar(api, ruta, carga).status_code == 400

    def test_rechaza_un_contrato_mas_nuevo_que_el_servicio(
        self, api, ruta, carga, servicio_configurado
    ):
        """
        Una caja más moderna que el servicio recibe un mensaje claro.

        Sin esta comprobación, el síntoma sería un error de validación sobre
        un campo concreto, que no orienta a quien tenga que resolverlo.
        """
        carga["version"] = VERSION_CONTRATO + 1
        respuesta = _enviar(api, ruta, carga)
        assert respuesta.status_code == 400
        assert "servicio" in json.dumps(respuesta.json()).lower()

    def test_ignora_campos_no_declarados_en_el_contrato(
        self, api, ruta, carga, servicio_configurado
    ):
        """
        Lo que no está en el contrato no llega a la base de datos.

        Acota el daño de una caja comprometida: no puede escribir atributos
        que el servicio no haya previsto.
        """
        carga["placa"] = "ABC1234"
        carga["operador"] = "cajero"
        assert _enviar(api, ruta, carga).status_code == 201

        boleto = BoletoPublicado.objects.get()
        assert not hasattr(boleto, "placa")
        assert "ABC1234" not in boleto.placa_enmascarada

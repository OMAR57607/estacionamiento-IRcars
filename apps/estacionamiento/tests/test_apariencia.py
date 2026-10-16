"""
Pruebas de la presentación de la aplicación.

No comprueban que algo sea bonito —eso no se prueba— sino las decisiones de
interfaz que, si se revierten, no rompen ninguna funcionalidad y por tanto
nadie nota hasta que estorban en la caseta: el icono al añadir la aplicación
a la pantalla de inicio, la navegación alcanzable con el pulgar y la ausencia
de colores fijos que anularían el tema oscuro.
"""
import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db

ESTATICOS = Path(settings.BASE_DIR) / "apps/estacionamiento/static/estacionamiento"
CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def sesion(client):
    cajero = get_user_model().objects.create_user(
        username="cajero", password=CLAVE, is_staff=True
    )
    client.force_login(cajero)
    return client


class TestIcono:
    def test_existen_los_tamanos_que_pide_cada_sistema(self):
        """
        Cuatro tamaños, y cada uno tiene su motivo.

        32 px para la pestaña del navegador, 180 porque es lo que exige iOS al
        añadir a la pantalla de inicio —sin él muestra una captura de la
        página, ilegible a ese tamaño— y 192 y 512 para el manifiesto de
        Android.

        No hay SVG: el logotipo es un mapa de bits. Declarar uno obligaría a
        incrustar el mismo mapa dentro de un envoltorio vectorial, que no
        gana nitidez y sí pesa más.
        """
        for lado in (32, 180, 192, 512):
            nombre = f"icono-{lado}.png"
            assert (ESTATICOS / nombre).exists(), f"Falta {nombre}"

    @pytest.mark.parametrize("lado", [32, 180, 192, 512])
    def test_los_png_son_validos_y_del_tamano_declarado(self, lado):
        """
        Se generan con `scripts/generar_iconos.py` a partir del original.

        Un icono que no sea un PNG válido o que mienta sobre su tamaño
        produce un hueco en la pantalla de inicio, sin ningún error visible.
        """
        import struct

        datos = (ESTATICOS / f"icono-{lado}.png").read_bytes()

        assert datos[:8] == b"\x89PNG\r\n\x1a\n", "No es un PNG"
        ancho, alto = struct.unpack(">II", datos[16:24])
        assert (ancho, alto) == (lado, lado)

    def test_el_manifiesto_es_json_valido_y_apunta_a_los_iconos(self):
        manifiesto = json.loads((ESTATICOS / "manifiesto.json").read_text(encoding="utf-8"))

        assert manifiesto["start_url"] == "/"
        assert manifiesto["display"] == "standalone"

        for icono in manifiesto["icons"]:
            assert (ESTATICOS / icono["src"]).exists(), icono["src"]

    def test_la_plantilla_declara_el_icono_y_el_manifiesto(self, sesion):
        contenido = sesion.get(reverse("web:caja"), secure=True).content.decode()

        assert 'rel="icon"' in contenido
        assert 'rel="apple-touch-icon"' in contenido
        assert 'rel="manifest"' in contenido
        assert 'name="theme-color"' in contenido
        assert ".svg" not in contenido, (
            "El logotipo es un mapa de bits: un SVG aquí solo envolvería el mismo "
            "dibujo con más peso"
        )


class TestNavegacion:
    def test_hay_navegacion_al_alcance_del_pulgar(self, sesion):
        """
        El operador sostiene el teléfono con una mano mientras atiende.

        La parte baja de la pantalla es la única zona que el pulgar alcanza
        sin recolocar el aparato, y por eso las secciones viven ahí en
        pantalla estrecha.
        """
        contenido = sesion.get(reverse("web:caja"), secure=True).content.decode()

        assert "barra-inferior" in contenido
        for destino in ("web:caja", "web:turno", "web:tarifa"):
            assert reverse(destino) in contenido

    def test_los_iconos_no_dependen_de_la_fuente_del_telefono(self, sesion):
        """
        Dibujados en línea, no con caracteres Unicode.

        Los glifos geométricos los resuelve cada teléfono con la fuente que
        tenga: el mismo icono sale de un grosor distinto en cada aparato y en
        algunos no existe.
        """
        contenido = sesion.get(reverse("web:caja"), secure=True).content.decode()

        assert contenido.count("<svg") >= 3
        assert 'stroke="currentColor"' in contenido, (
            "El icono debe heredar el color del enlace activo"
        )

    def test_la_navegacion_no_se_imprime(self, sesion):
        """El comprobante de corte se firma en papel y no lleva menús."""
        contenido = sesion.get(reverse("web:caja"), secure=True).content.decode()

        assert 'class="barra-inferior no-imprimir"' in contenido


class TestTemaOscuro:
    def test_la_hoja_de_estilos_define_el_tema_oscuro(self):
        css = (ESTATICOS / "estilos.css").read_text(encoding="utf-8")
        assert "prefers-color-scheme: dark" in css

    def test_ninguna_plantilla_fija_colores_en_linea(self):
        """
        Un color escrito en un atributo `style` ignora el tema.

        Basta uno para que una tarjeta quede con el borde azul claro sobre
        fondo oscuro, o un texto negro sobre negro. El color va por tokens,
        que es lo que permite que el tema oscuro sea un bloque de
        redefiniciones y no una segunda hoja que se desincroniza.
        """
        plantillas = Path(settings.BASE_DIR) / "apps/estacionamiento/templates"
        patron = re.compile(r'style="[^"]*#[0-9a-fA-F]{3,6}')

        culpables = [
            f"{ruta.name}: {m.group(0)}"
            for ruta in plantillas.rglob("*.html")
            for m in patron.finditer(ruta.read_text(encoding="utf-8"))
        ]

        assert not culpables, "Colores fijos en plantillas: " + "; ".join(culpables)

    def test_el_codigo_qr_conserva_su_fondo_blanco(self):
        """
        El marco del QR es blanco también en tema oscuro, a propósito.

        Un código invertido no lo reconoce todo lector, y el boleto dejaría de
        escanearse justo en la rampa de salida.
        """
        css = (ESTATICOS / "estilos.css").read_text(encoding="utf-8")
        bloque = css[css.index(".qr-marco {") : css.index(".qr-marco svg")]
        assert "background: #fff" in bloque


@pytest.mark.django_db
class TestMarcaEnLosBoletos:
    """
    El logotipo va en las dos caras del boleto.

    La del cliente, porque es lo que recibe por WhatsApp o abre al escanear y
    debe reconocer de quién es. La del operador, porque esa pantalla se pone
    delante del cliente y, si el código no se lee y hay que enseñar el
    teléfono, lo que se ve tiene que identificar al estacionamiento.
    """

    def test_el_boleto_del_cliente_lleva_la_marca(self, client, settings):
        from datetime import timedelta
        from decimal import Decimal

        from django.template.loader import render_to_string
        from django.utils import timezone

        from apps.boletos.presentacion import BoletoVista

        ahora = timezone.now()
        vista = BoletoVista(
            uuid_folio="0a9f6b1c-0000-4000-8000-000000000000",
            placa_enmascarada="ABC••••",
            estado="ABIERTO",
            ubicacion="A-01",
            entrada_en=ahora - timedelta(hours=1),
            salida_en=None,
            minutos=None,
            importe=None,
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=15,
            fraccion_minutos=1,
            cobro_minimo=Decimal("0.00"),
            tope_diario=None,
            fin_de_ciclo=None,
        )
        dibujado = render_to_string(
            "boletos/boleto.html",
            {
                "boleto": vista,
                "nombre_comercial": "Pruebas",
                "consultado_en": ahora,
                "minutos": 60,
                "importe": Decimal("30.00"),
                "es_estimado": True,
                "cotizacion": None,
            },
        )

        assert "icono-192.png" in dibujado
        assert "Pruebas" in dibujado, (
            "El nombre se conserva: si la imagen no carga, la página sigue "
            "identificando al estacionamiento"
        )

    def test_el_boleto_del_operador_lleva_la_marca(self, sesion, folio_de_prueba):
        from django.urls import reverse

        ruta = reverse("web:boleto", kwargs={"uuid": folio_de_prueba.uuid})
        contenido = sesion.get(ruta, secure=True).content.decode()

        assert "icono-192.png" in contenido

    def test_el_boleto_del_cliente_declara_el_icono_de_la_pestana(self, client, folio_de_prueba):
        """
        La página del cliente no hereda de `base.html`: declara su propio head.

        Cuando no declaraba icono, el teléfono pedía `/favicon.ico` a la raíz
        del sitio y recibía un 404 —visible en los registros de producción—.
        La pestaña quedaba con el icono genérico justo en la pantalla que el
        cliente conserva hasta la salida.
        """
        from django.urls import reverse

        ruta = reverse("boletos:publico", kwargs={"uuid_folio": folio_de_prueba.uuid})
        contenido = client.get(ruta, secure=True).content.decode()

        assert 'rel="icon"' in contenido
        assert 'rel="apple-touch-icon"' in contenido


@pytest.fixture
def folio_de_prueba(db):
    from datetime import timedelta
    from decimal import Decimal

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from apps.estacionamiento import services
    from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon

    Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        vigente_desde=timezone.now() - timedelta(days=1),
    )
    Cajon.objects.create(codigo="A-01", tipo=TipoCajon.AUTOMOVIL)
    operador = get_user_model().objects.create_user(username="otro", password=CLAVE)
    return services.registrar_entrada(placa="ABC1234", operador=operador)

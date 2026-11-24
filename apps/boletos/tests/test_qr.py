"""
Pruebas de la generación del código QR.

Lo que se verifica no es el símbolo en sí —eso es responsabilidad de la
biblioteca— sino lo que el proyecto decide: qué dirección se codifica, que no
se genere un QR apuntando a ninguna parte, y que el formato de salida sea el
que la ventana de la caja puede mostrar.
"""
import pytest

from apps.boletos import qr


class TestDireccion:
    def test_codifica_la_direccion_publica_del_boleto(self, servicio_configurado):
        url = qr.url_boleto("0a9f6b1c-0000-4000-8000-000000000000")
        assert url == (
            "https://boletos.ejemplo.mx/t/0a9f6b1c-0000-4000-8000-000000000000/"
        )

    def test_tolera_una_barra_final_en_la_configuracion(self, servicio_configurado):
        """
        Un `/` de más al configurar no debe producir una dirección con `//`.

        Es el error de captura más probable al pegar el dominio, y el síntoma
        sería un QR que lleva a un 404 sin explicación.
        """
        servicio_configurado.IRCARS_URL_BOLETOS = "https://boletos.ejemplo.mx/"
        assert "//t/" not in qr.url_boleto("abc")

    def test_sin_origen_configurado_no_se_genera_un_qr_invalido(
        self, servicio_configurado
    ):
        """
        Sin dirección, se falla de forma explícita.

        Generar el símbolo de una dirección incompleta sería peor que no
        generarlo: el cliente escanearía, no llegaría a nada y el operador no
        sabría por qué.
        """
        servicio_configurado.IRCARS_URL_BOLETOS = ""
        with pytest.raises(qr.QRNoDisponible):
            qr.url_boleto("abc")


class TestFormatos:
    def test_el_png_es_un_png(self, servicio_configurado):
        """
        Tk 8.6 muestra PNG de forma nativa, sin Pillow ni archivos temporales.

        Se comprueba la firma del formato porque es de lo que depende que la
        ventana de la caja pueda mostrar el símbolo.
        """
        datos = qr.png_qr("https://boletos.ejemplo.mx/t/abc/")
        assert datos.startswith(b"\x89PNG\r\n\x1a\n")

    def test_el_png_lleva_zona_de_silencio(self, servicio_configurado):
        """
        El borde no es decorativo: sin él, muchos lectores no reconocen el
        símbolo contra un fondo contiguo.
        """
        con_borde = qr.png_qr("https://boletos.ejemplo.mx/t/abc/", borde=4)
        sin_borde = qr.png_qr("https://boletos.ejemplo.mx/t/abc/", borde=0)
        assert len(con_borde) != len(sin_borde)

    def test_el_svg_se_incrusta_sin_cabecera_de_documento(self, servicio_configurado):
        """
        El SVG se inserta dentro de una plantilla HTML, no se sirve como
        archivo propio: no debe traer declaración XML.
        """
        marcado = qr.svg_qr("https://boletos.ejemplo.mx/t/abc/")
        assert marcado.lstrip().startswith("<svg")
        assert "<?xml" not in marcado


class TestLogotipo:
    """
    El logotipo al centro del código.

    Que el símbolo se siga leyendo con el logotipo encima se midió con lectores
    reales (`scripts/verificar_qr_logo.py`, que necesita bibliotecas que no
    son del proyecto). Aquí se fija lo que hace posible ese resultado: el
    nivel de corrección, el tamaño y la posición del recuadro.
    """

    URL = "https://boletos.ejemplo.mx/t/60b86282-1bc8-4764-82c9-ac647d8d62d6/"

    def test_sin_logotipo_el_codigo_no_cambia(self, servicio_configurado):
        """Quien no pide logotipo recibe el mismo símbolo de nivel medio de antes."""
        marcado = qr.svg_qr(self.URL)
        assert "<image" not in marcado
        assert marcado == qr._simbolo(self.URL).svg_inline(scale=5)

    def test_con_logotipo_sube_a_correccion_alta(self, servicio_configurado):
        """
        El logotipo tapa módulos: sin la redundancia del nivel alto, una arruga
        o un reflejo bastarían para que el código dejara de leerse.
        """
        assert qr.CORRECCION_CON_LOGO == "h"
        alto = qr._simbolo(self.URL, correccion=qr.CORRECCION_CON_LOGO)
        lado = (alto.symbol_size(border=0)[0] + 2 * qr.BORDE_SVG) * 6
        marcado = qr.svg_qr(self.URL, escala=6, logo_url="/static/logo.png")
        assert f'width="{lado}"' in marcado, "El símbolo no es el de nivel alto"

    def test_el_logotipo_va_dentro_del_svg_y_escala_con_el(self, servicio_configurado):
        """
        Sin `viewBox`, la hoja de estilos que limita el ancho recortaba el
        código en lugar de reducirlo: con el nivel alto ya no cabía entero.
        """
        marcado = qr.svg_qr(self.URL, escala=6, logo_url="/static/logo.png")
        assert "viewBox=" in marcado
        assert '<image href="/static/logo.png"' in marcado
        assert marcado.rstrip().endswith("</svg>")

    def test_la_direccion_del_logotipo_se_escapa(self, servicio_configurado):
        """Va dentro de un atributo: unas comillas no deben poder cerrarlo."""
        marcado = qr.svg_qr(self.URL, logo_url='/s/a"onload="x.png')
        assert 'a"onload' not in marcado
        assert "&quot;" in marcado

    @pytest.mark.parametrize("version", range(1, 41))
    def test_el_recuadro_queda_centrado_y_lejos_de_las_esquinas(self, version):
        """
        Centrado exacto —mismos módulos a cada lado— y sin tocar los patrones
        de las esquinas ni su separador, que son lo que el lector busca
        primero para encontrar el código.
        """
        modulos = 17 + 4 * version
        lado = qr.modulos_del_logo(modulos)
        margen = (modulos - lado) // 2
        assert margen * 2 + lado == modulos
        assert margen > 8
        assert lado / modulos <= qr.PROPORCION_LOGO + 0.02

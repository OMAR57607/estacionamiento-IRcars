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

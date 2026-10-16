"""
Pruebas del enmascarado de placas.

Es la única salvaguarda que impide publicar un dato personal en una dirección
sin contraseña, por lo que se verifica como lógica pura: sin base de datos, sin
Django y sin peticiones HTTP.
"""
import pytest

from apps.boletos.dominio import RELLENO, enmascarar_placa, normalizar_placa


class TestNormalizacion:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("abc1234", "ABC1234"),
            ("  ABC 12 34  ", "ABC1234"),
            ("aBc-123", "ABC-123"),
            (None, ""),
            ("", ""),
        ],
    )
    def test_coincide_con_la_forma_canonica_del_folio(self, entrada, esperado):
        """
        Debe producir lo mismo que `Folio.save`: mayúsculas y sin espacios.

        Si divergieran, el boleto publicado mostraría una placa con un formato
        distinto al registrado y el cliente dudaría de que sea el suyo.
        """
        assert normalizar_placa(entrada) == esperado


class TestEnmascarado:
    @pytest.mark.parametrize(
        "placa,esperado",
        [
            ("ABC1234", f"ABC{RELLENO * 4}"),
            ("abc 12 34", f"ABC{RELLENO * 4}"),
            ("12ABC", f"12{RELLENO * 3}"),
            ("ABC", f"A{RELLENO * 2}"),
        ],
    )
    def test_oculta_la_parte_individualizante(self, placa, esperado):
        assert enmascarar_placa(placa) == esperado

    def test_conserva_la_longitud(self):
        """
        La longitud se mantiene para que el cliente reconozca su boleto.

        Es el equilibrio buscado: suficiente para identificarlo, insuficiente
        para reconstruir el dato.
        """
        assert len(enmascarar_placa("ABC1234")) == len("ABC1234")

    @pytest.mark.parametrize(
        "placa", ["A", "AB", "ABC", "AB12", "12ABC", "ABC123", "ABC1234", "ABCD12345"]
    )
    def test_nunca_revela_mas_de_la_mitad(self, placa):
        """
        Invariante central: como máximo la mitad de los caracteres a la vista.

        Se comprueba sobre todas las longitudes plausibles porque el riesgo
        está justamente en las cortas, donde una ventana fija de tres
        caracteres dejaría la placa casi entera expuesta.
        """
        mascara = enmascarar_placa(placa)
        visibles = sum(1 for caracter in mascara if caracter != RELLENO)
        assert visibles <= len(placa) // 2

    def test_el_invariante_no_se_puede_eludir_con_el_parametro(self):
        """
        Pedir una ventana mayor no amplía la fuga.

        El techo vive en la función y no en quien la llama: así una vista
        futura que invoque `enmascarar_placa(placa, visibles=10)` por descuido
        no publica la placa completa.
        """
        mascara = enmascarar_placa("ABC1234", visibles=99)
        visibles = sum(1 for caracter in mascara if caracter != RELLENO)
        assert visibles <= 3
        assert RELLENO in mascara

    def test_nunca_devuelve_la_placa_intacta(self):
        for placa in ("ABC1234", "12ABC", "ABC", "AB"):
            assert enmascarar_placa(placa) != normalizar_placa(placa)

    @pytest.mark.parametrize("vacia", [None, "", "   "])
    def test_placa_ausente_no_revienta(self, vacia):
        assert enmascarar_placa(vacia) == ""

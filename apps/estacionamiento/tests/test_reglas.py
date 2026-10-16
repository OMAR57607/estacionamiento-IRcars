"""
Pruebas del cuadro tarifario dicho en palabras.

Una frase que describa mal el cobro es tan dañina como un importe mal
calculado: se pone delante de quien fija el precio para que detecte un desvío,
y si miente, lo confirma.
"""
from decimal import Decimal

import pytest

from apps.estacionamiento import reglas
from apps.estacionamiento.domain.tarifas import ParametrosTarifa


def frase_con(frases, fragmento):
    return any(fragmento in f for f in frases)


class TestEnPalabras:
    def test_describe_la_regla_del_negocio(self):
        """
        El caso que se quiso configurar: $15/hora con 15 de tolerancia.
        """
        frases = reglas.en_palabras(
            ParametrosTarifa(
                cuota_hora=Decimal("15.00"),
                minutos_cortesia=0,
                fraccion_minutos=1,
                minutos_tolerancia=15,
                cobro_minimo=Decimal("15.00"),
                tope_diario=Decimal("250.00"),
            )
        )

        assert frase_con(frases, "$15.00 por cada hora")
        assert frase_con(frases, "no hay minutos gratis")
        assert frase_con(frases, "15 minutos de tolerancia")
        assert frase_con(frases, "la hora completa")
        assert frase_con(frases, "al minuto exacto")
        assert frase_con(frases, "menos de $15.00")
        assert frase_con(frases, "$250.00 por cada día")

    def test_distingue_el_redondeo_de_la_tolerancia(self):
        """
        El desvío real de producción: un 15 tecleado en el campo equivocado.

        El precio quedó con bloques de 15 minutos y sin tolerancia, que no es
        inválido —el motor cobró exactamente eso— pero cobra otra cosa. Las
        frases tienen que decirlo con todas sus letras, porque es lo único que
        distingue una captura de la otra a simple vista.
        """
        frases = reglas.en_palabras(
            ParametrosTarifa(
                cuota_hora=Decimal("15.00"),
                minutos_cortesia=0,
                fraccion_minutos=15,
                minutos_tolerancia=0,
                cobro_minimo=Decimal("15.00"),
                tope_diario=Decimal("250.00"),
            )
        )

        assert frase_con(frases, "No hay tolerancia por hora")
        assert frase_con(frases, "bloques de 15 minutos")
        assert not frase_con(frases, "de tolerancia: dentro de ellos")

    def test_menciona_la_cortesia_solo_cuando_existe(self):
        con = reglas.en_palabras(
            ParametrosTarifa(cuota_hora=Decimal("30.00"), minutos_cortesia=15)
        )
        sin = reglas.en_palabras(
            ParametrosTarifa(cuota_hora=Decimal("30.00"), minutos_cortesia=0)
        )

        assert frase_con(con, "Los primeros 15 minutos no se cobran")
        assert frase_con(sin, "desde el primer minuto")

    def test_omite_el_minimo_y_el_tope_cuando_no_se_usan(self):
        """
        Una frase de más sobre un parámetro inactivo es ruido que resta
        atención a las que sí describen el cobro.
        """
        frases = reglas.en_palabras(
            ParametrosTarifa(cuota_hora=Decimal("30.00"), cobro_minimo=Decimal("0.00"))
        )

        assert not frase_con(frases, "menos de")
        assert not frase_con(frases, "por cada día")

    @pytest.mark.parametrize(
        "parametros",
        [
            ParametrosTarifa(cuota_hora=Decimal("15.00"), minutos_tolerancia=15),
            ParametrosTarifa(cuota_hora=Decimal("15.00"), fraccion_minutos=15),
            ParametrosTarifa(cuota_hora=Decimal("0.00")),
        ],
    )
    def test_siempre_dice_algo_y_termina_cada_frase(self, parametros):
        frases = reglas.en_palabras(parametros)

        assert len(frases) >= 3
        for frase in frases:
            assert frase.endswith("."), frase

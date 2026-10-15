"""
Pruebas del motor de tarificación.

No requieren base de datos ni Django: el motor es lógica pura. Esto permite
ejecutarlas en milisegundos y cubrir exhaustivamente los casos frontera, que
es precisamente donde se originan las inconformidades de cobro.
"""
from decimal import Decimal

import pytest

from apps.estacionamiento.domain.exceptions import ErrorConfiguracion
from apps.estacionamiento.domain.tarifas import ParametrosTarifa, cotizar

# Tarifa de referencia: $30/hora, 15 minutos de cortesía, cobro por minuto
# real, tope de $250 por día.
TARIFA_BASE = ParametrosTarifa(
    cuota_hora=Decimal("30.00"),
    minutos_cortesia=15,
    fraccion_minutos=1,
    cobro_minimo=Decimal("0.00"),
    tope_diario=Decimal("250.00"),
)


class TestCortesia:
    def test_dentro_de_la_cortesia_no_se_cobra(self):
        cot = cotizar(10, TARIFA_BASE)
        assert cot.importe == Decimal("0.00")
        assert cot.es_cortesia is True

    def test_el_limite_exacto_de_cortesia_sigue_siendo_gratuito(self):
        # Frontera inclusiva: 15 minutos con 15 de cortesía no se cobra.
        cot = cotizar(15, TARIFA_BASE)
        assert cot.importe == Decimal("0.00")
        assert cot.es_cortesia is True

    def test_un_minuto_despues_de_la_cortesia_ya_se_cobra(self):
        cot = cotizar(16, TARIFA_BASE)
        assert cot.es_cortesia is False
        # 16 minutos * ($30/60) = $8.00
        assert cot.importe == Decimal("8.00")


class TestProporcionalidad:
    def test_una_hora_completa_cobra_la_cuota_horaria(self):
        assert cotizar(60, TARIFA_BASE).importe == Decimal("30.00")

    def test_media_hora_cobra_la_mitad(self):
        assert cotizar(30, TARIFA_BASE).importe == Decimal("15.00")

    def test_el_cobro_es_estrictamente_proporcional_al_minuto(self):
        # 90 minutos = 1.5 horas = $45.00. Sin redondeo a hora completa:
        # este es el comportamiento exigido por el criterio de cobro
        # proporcional, y la razón de que `fraccion_minutos` valga 1.
        assert cotizar(90, TARIFA_BASE).importe == Decimal("45.00")

    def test_fraccion_en_bloques_redondea_el_bloque_iniciado(self):
        tarifa = ParametrosTarifa(
            cuota_hora=Decimal("30.00"), fraccion_minutos=15, minutos_cortesia=0
        )
        # 31 minutos ocupan 3 bloques de 15 min: 3 * $7.50 = $22.50
        cot = cotizar(31, tarifa)
        assert cot.bloques_cobrados == 3
        assert cot.importe == Decimal("22.50")


class TestCobroMinimo:
    def test_el_cobro_minimo_actua_como_piso(self):
        tarifa = ParametrosTarifa(
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=0,
            cobro_minimo=Decimal("20.00"),
        )
        # 10 minutos proporcionales serían $5.00, pero el mínimo es $20.00.
        cot = cotizar(10, tarifa)
        assert cot.importe == Decimal("20.00")
        assert cot.cobro_minimo_aplicado is True

    def test_el_cobro_minimo_no_se_aplica_en_cortesia(self):
        tarifa = ParametrosTarifa(
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=15,
            cobro_minimo=Decimal("20.00"),
        )
        cot = cotizar(10, tarifa)
        assert cot.importe == Decimal("0.00")
        assert cot.cobro_minimo_aplicado is False


class TestTopeDiario:
    def test_el_tope_acota_una_permanencia_prolongada(self):
        # 20 horas proporcionales = $600.00, pero el tope diario es $250.00.
        cot = cotizar(20 * 60, TARIFA_BASE)
        assert cot.importe == Decimal("250.00")
        assert cot.tope_diario_aplicado is True

    def test_el_tope_se_multiplica_por_dia_natural_iniciado(self):
        # 25 horas invaden el segundo día: el techo es 2 * $250 = $500,
        # y el proporcional (25 * $30 = $750) queda por encima.
        cot = cotizar(25 * 60, TARIFA_BASE)
        assert cot.importe == Decimal("500.00")
        assert cot.tope_diario_aplicado is True

    def test_sin_tope_el_cobro_crece_de_forma_lineal(self):
        tarifa = ParametrosTarifa(
            cuota_hora=Decimal("30.00"), minutos_cortesia=0, tope_diario=None
        )
        assert cotizar(20 * 60, tarifa).importe == Decimal("600.00")
        assert cotizar(20 * 60, tarifa).tope_diario_aplicado is False

    def test_el_tope_prevalece_sobre_el_cobro_minimo(self):
        tarifa = ParametrosTarifa(
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=0,
            cobro_minimo=Decimal("100.00"),
            tope_diario=Decimal("40.00"),
        )
        cot = cotizar(5, tarifa)
        assert cot.importe == Decimal("40.00")
        assert cot.tope_diario_aplicado is True
        assert cot.cobro_minimo_aplicado is False


class TestPrecisionMonetaria:
    def test_el_importe_siempre_tiene_dos_decimales(self):
        tarifa = ParametrosTarifa(cuota_hora=Decimal("37.00"), minutos_cortesia=0)
        # 7 minutos: 37 * 7 / 60 = 4.31666... -> $4.32 (ROUND_HALF_UP)
        cot = cotizar(7, tarifa)
        assert cot.importe == Decimal("4.32")
        assert cot.importe.as_tuple().exponent == -2

    def test_cuota_cero_produce_importe_cero(self):
        tarifa = ParametrosTarifa(cuota_hora=Decimal("0.00"), minutos_cortesia=0)
        assert cotizar(120, tarifa).importe == Decimal("0.00")


class TestValidacionDeParametros:
    def test_se_rechaza_una_cuota_negativa(self):
        with pytest.raises(ErrorConfiguracion):
            ParametrosTarifa(cuota_hora=Decimal("-1.00"))

    def test_se_rechaza_una_fraccion_menor_a_un_minuto(self):
        with pytest.raises(ErrorConfiguracion):
            ParametrosTarifa(cuota_hora=Decimal("30.00"), fraccion_minutos=0)

    def test_se_rechaza_una_fraccion_mayor_a_una_hora(self):
        # Un bloque superior a 60 minutos rompe la proporcionalidad del cobro.
        with pytest.raises(ErrorConfiguracion):
            ParametrosTarifa(cuota_hora=Decimal("30.00"), fraccion_minutos=90)

    def test_se_rechaza_una_permanencia_negativa(self):
        with pytest.raises(ErrorConfiguracion):
            cotizar(-5, TARIFA_BASE)

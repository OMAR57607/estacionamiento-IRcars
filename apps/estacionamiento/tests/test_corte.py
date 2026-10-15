"""
Pruebas del motor de corte de caja.

Lógica pura: no requieren base de datos. Verifican la cifra que sustenta una
conversación incómoda con un operador, por lo que la cobertura de fronteras
—sobrante, faltante, cuadre exacto, cortesías— no es opcional.
"""
from decimal import Decimal

from apps.estacionamiento.domain.corte import calcular_corte


class TestEfectivoEsperado:
    def test_sin_movimientos_el_esperado_es_el_fondo(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("500.00"), importes_cobrados=[]
        )
        assert corte.efectivo_esperado == Decimal("500.00")
        assert corte.total_cobrado == Decimal("0.00")
        assert corte.folios_cobrados == 0

    def test_el_esperado_suma_fondo_y_cobros(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("500.00"),
            importes_cobrados=[Decimal("30.00"), Decimal("45.50"), Decimal("15.00")],
        )
        assert corte.total_cobrado == Decimal("90.50")
        assert corte.efectivo_esperado == Decimal("590.50")
        assert corte.folios_cobrados == 3


class TestCortesias:
    def test_las_cortesias_no_mueven_efectivo_pero_se_cuentan(self):
        # Un turno con muchas cortesías es un indicador de control: el volumen
        # debe ser visible aunque el importe sea cero.
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"),
            importes_cobrados=[
                Decimal("30.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("20.00"),
            ],
        )
        assert corte.total_cobrado == Decimal("50.00")
        assert corte.folios_cobrados == 2
        assert corte.folios_cortesia == 2


class TestDiferencia:
    def test_sin_conteo_la_diferencia_es_indeterminada(self):
        # Devolver cero aquí afirmaría que la caja cuadra sin haber contado.
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"), importes_cobrados=[Decimal("50.00")]
        )
        assert corte.diferencia is None
        assert corte.efectivo_declarado is None
        assert corte.cuadra is False
        assert corte.hay_faltante is False
        assert corte.hay_sobrante is False

    def test_cuadre_exacto(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"),
            importes_cobrados=[Decimal("50.00")],
            efectivo_declarado=Decimal("150.00"),
        )
        assert corte.diferencia == Decimal("0.00")
        assert corte.cuadra is True

    def test_faltante_se_reporta_en_negativo(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"),
            importes_cobrados=[Decimal("50.00")],
            efectivo_declarado=Decimal("130.00"),
        )
        assert corte.diferencia == Decimal("-20.00")
        assert corte.hay_faltante is True
        assert corte.cuadra is False

    def test_sobrante_se_reporta_en_positivo(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"),
            importes_cobrados=[Decimal("50.00")],
            efectivo_declarado=Decimal("165.50"),
        )
        assert corte.diferencia == Decimal("15.50")
        assert corte.hay_sobrante is True

    def test_un_conteo_en_cero_no_se_confunde_con_la_ausencia_de_conteo(self):
        # Declarar $0.00 es un dato (caja vacía, probable faltante total);
        # no declarar nada es otra cosa. Confundirlos ocultaría un robo.
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"),
            importes_cobrados=[Decimal("50.00")],
            efectivo_declarado=Decimal("0.00"),
        )
        assert corte.diferencia == Decimal("-150.00")
        assert corte.hay_faltante is True


class TestPrecisionMonetaria:
    def test_los_montos_conservan_dos_decimales(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("0.00"),
            importes_cobrados=[Decimal("4.32"), Decimal("8.01"), Decimal("0.67")],
            efectivo_declarado=Decimal("13.00"),
        )
        assert corte.total_cobrado == Decimal("13.00")
        assert corte.diferencia == Decimal("0.00")
        assert corte.diferencia.as_tuple().exponent == -2


class TestSerializacion:
    def test_el_resumen_es_serializable_para_el_comprobante(self):
        corte = calcular_corte(
            fondo_inicial=Decimal("100.00"),
            importes_cobrados=[Decimal("30.00"), Decimal("0.00")],
            efectivo_declarado=Decimal("125.00"),
        )
        datos = corte.a_dict()
        assert datos["fondo_inicial"] == "100.00"
        assert datos["total_cobrado"] == "30.00"
        assert datos["efectivo_esperado"] == "130.00"
        assert datos["diferencia"] == "-5.00"
        assert datos["folios_cortesia"] == 1
        assert datos["cuadra"] is False

    def test_sin_conteo_el_resumen_reporta_nulos_explicitos(self):
        datos = calcular_corte(
            fondo_inicial=Decimal("100.00"), importes_cobrados=[]
        ).a_dict()
        assert datos["efectivo_declarado"] is None
        assert datos["diferencia"] is None

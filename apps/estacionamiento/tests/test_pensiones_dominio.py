"""
Motor de la pensión diaria, sin base de datos.

Fija las reglas que confirmó el dueño (docs/REDISENO.md §2): un día son 24
horas, el excedente de un minuto completo se cobra por hora y sin cortesía, y
renovar no deja huecos.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from apps.estacionamiento.domain import pensiones as d
from apps.estacionamiento.domain.exceptions import ErrorConfiguracion
from apps.estacionamiento.domain.tarifas import ParametrosTarifa

UTC = timezone.utc
T0 = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
AUTO = ParametrosTarifa(
    cuota_hora=Decimal("30.00"), minutos_cortesia=15, fraccion_minutos=1
)


class TestPeriodo:
    def test_sin_pago_previo_empieza_al_pagar(self):
        desde, hasta = d.periodo_siguiente(ahora=T0, vigente_hasta=None, dias=1)
        assert (desde, hasta) == (T0, T0 + timedelta(hours=24))

    def test_un_dia_son_24_horas_y_no_un_dia_natural(self):
        a_las_22 = T0.replace(hour=22)
        _, hasta = d.periodo_siguiente(ahora=a_las_22, vigente_hasta=None, dias=1)
        assert hasta == a_las_22 + timedelta(hours=24)

    def test_vigente_continua_sin_hueco_ni_traslape(self):
        vence = T0 + timedelta(hours=5)
        desde, hasta = d.periodo_siguiente(ahora=T0, vigente_hasta=vence, dias=2)
        assert desde == vence
        assert hasta == vence + timedelta(days=2)

    def test_vencida_con_el_vehiculo_fuera_empieza_al_pagar(self):
        desde, _ = d.periodo_siguiente(
            ahora=T0, vigente_hasta=T0 - timedelta(hours=3), dias=1
        )
        assert desde == T0

    def test_vencida_con_el_vehiculo_dentro_empieza_al_vencimiento(self):
        """«A menos que renueve»: la renovación cubre el excedente."""
        vencio = T0 - timedelta(hours=3)
        entro = T0 - timedelta(hours=10)
        desde, _ = d.periodo_siguiente(
            ahora=T0, vigente_hasta=vencio, dias=1, inicio_estancia=entro
        )
        assert desde == vencio

    def test_si_entro_ya_vencida_empieza_a_la_entrada(self):
        vencio = T0 - timedelta(hours=10)
        entro = T0 - timedelta(hours=2)
        desde, _ = d.periodo_siguiente(
            ahora=T0, vigente_hasta=vencio, dias=1, inicio_estancia=entro
        )
        assert desde == entro

    def test_alta_con_el_vehiculo_dentro_lo_cubre_desde_su_entrada(self):
        entro = T0 - timedelta(hours=2)
        desde, _ = d.periodo_siguiente(
            ahora=T0, vigente_hasta=None, dias=1, inicio_estancia=entro
        )
        assert desde == entro

    def test_dos_renovaciones_seguidas_encadenan(self):
        _, h1 = d.periodo_siguiente(ahora=T0, vigente_hasta=None, dias=1)
        d2, h2 = d.periodo_siguiente(ahora=T0 + timedelta(hours=1), vigente_hasta=h1, dias=1)
        assert d2 == h1
        assert h2 - T0 == timedelta(days=2)

    @pytest.mark.parametrize("dias", [0, -1, 32])
    def test_dias_fuera_de_rango(self, dias):
        with pytest.raises(ErrorConfiguracion):
            d.periodo_siguiente(ahora=T0, vigente_hasta=None, dias=dias)


class TestVigencia:
    def test_cubre_hasta_el_instante_anterior_al_vencimiento(self):
        vence = T0
        assert d.cubre(momento=vence - timedelta(seconds=1), vigente_hasta=vence)
        assert not d.cubre(momento=vence, vigente_hasta=vence)
        assert not d.cubre(momento=T0, vigente_hasta=None)

    def test_estados(self):
        aviso = timedelta(hours=2)
        estado = lambda hasta: d.estado_vigencia(ahora=T0, vigente_hasta=hasta, aviso=aviso)
        assert estado(None) is d.EstadoVigencia.SIN_PAGO
        assert estado(T0) is d.EstadoVigencia.VENCIDA
        assert estado(T0 + timedelta(hours=2)) is d.EstadoVigencia.POR_VENCER
        assert estado(T0 + timedelta(hours=2, minutes=1)) is d.EstadoVigencia.VIGENTE

    def test_monto(self):
        assert d.monto_del_periodo(Decimal("180.00"), 1) == Decimal("180.00")
        assert d.monto_del_periodo(Decimal("180.00"), 7) == Decimal("1260.00")


class TestSalida:
    def test_dentro_de_la_vigencia_no_se_cobra(self):
        salida = d.cotizar_salida(
            entrada=T0, salida=T0 + timedelta(hours=10),
            vigente_hasta=T0 + timedelta(hours=24), parametros=AUTO,
        )
        assert salida.importe == Decimal("0.00")
        assert salida.cubierta
        assert salida.minutos_totales == 600

    def test_pasarse_59_segundos_no_es_pasarse_un_minuto(self):
        vence = T0 + timedelta(hours=24)
        salida = d.cotizar_salida(
            entrada=T0, salida=vence + timedelta(seconds=59),
            vigente_hasta=vence, parametros=AUTO,
        )
        assert salida.importe == Decimal("0.00")

    def test_un_minuto_completo_se_cobra_por_hora_sin_cortesia(self):
        """La regla del dueño: si se pasa 1 minuto, entra la tarifa."""
        vence = T0 + timedelta(hours=24)
        salida = d.cotizar_salida(
            entrada=T0, salida=vence + timedelta(minutes=1),
            vigente_hasta=vence, parametros=AUTO,
        )
        assert salida.minutos_excedentes == 1
        assert salida.importe == Decimal("0.50")  # $30/h, un minuto
        assert not salida.cotizacion.es_cortesia

    def test_solo_se_cobra_el_excedente(self):
        vence = T0 + timedelta(hours=24)
        salida = d.cotizar_salida(
            entrada=T0, salida=vence + timedelta(minutes=90),
            vigente_hasta=vence, parametros=AUTO,
        )
        assert salida.importe == Decimal("45.00")
        assert salida.minutos_totales == 24 * 60 + 90

    def test_desglose_dice_hasta_cuando_cubria(self):
        vence = T0 + timedelta(hours=1)
        salida = d.cotizar_salida(
            entrada=T0, salida=vence + timedelta(minutes=5),
            vigente_hasta=vence, parametros=AUTO,
        )
        desglose = salida.desglose()
        assert desglose["regla"] == "pension"
        assert desglose["minutos_excedentes"] == 5
        assert desglose["vigente_hasta"] == vence.isoformat()

"""
Casos de uso de la pensión diaria, contra la base.

Lo que se fija aquí: que la integridad la sostenga la base además del código,
que el dinero caiga siempre en un turno, que la salida cubierta no se cuele en
el corte como cortesía y que la renovación con el vehículo dentro cubra el
excedente.
"""
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.estacionamiento import services, services_caja, services_pensiones as sp
from apps.estacionamiento.domain.exceptions import (
    PagoInsuficiente,
    PagoNoCancelable,
    PensionDeBaja,
    PensionYaExiste,
    SinTurnoAbierto,
)
from apps.estacionamiento.models import (
    EstadoFolio,
    EstadoPagoPension,
    EstadoPension,
    Folio,
    PagoPension,
    Pension,
    PlanPension,
    Tarifa,
    TipoVehiculo,
    Turno,
)

pytestmark = pytest.mark.django_db

PRECIO = Decimal("180.00")


@pytest.fixture
def cajero():
    Tarifa.objects.create(
        nombre="Auto",
        tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )
    return get_user_model().objects.create_user(username="cajero", password="x" * 12)


@pytest.fixture
def turno(cajero):
    return services_caja.abrir_turno(operador=cajero, fondo_inicial=Decimal("0.00"))


def _alta(cajero, placa="abc 123", tipo=TipoVehiculo.AUTOMOVIL):
    return sp.registrar_pension(
        placa=placa, tipo_vehiculo=tipo, nombre_cliente="Laura Méndez",
        telefono="55 5550-0142", operador=cajero,
    )


class TestPrecio:
    def test_la_migracion_siembra_180_por_dia(self):
        plan = PlanPension.objects.get(activo=True)
        assert plan.precio_diario == PRECIO

    def test_cambiar_el_precio_no_toca_pagos_anteriores(self, cajero, turno):
        pension = _alta(cajero)
        pago = sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
        sp.cambiar_precio(precio_diario=Decimal("200.00"), operador=cajero)

        pago.refresh_from_db()
        pension.refresh_from_db()
        assert pago.monto == PRECIO
        assert pago.plan_precio_diario == PRECIO
        assert pension.vigente_hasta == pago.periodo_hasta
        assert PlanPension.objects.filter(activo=True).count() == 1


class TestAlta:
    def test_normaliza_placa_y_telefono(self, cajero):
        pension = _alta(cajero)
        assert pension.placa == "ABC123"
        assert pension.telefono == "5555500142"
        assert pension.vigente_hasta is None

    def test_segunda_alta_activa_de_la_misma_placa_falla(self, cajero):
        _alta(cajero)
        with pytest.raises(PensionYaExiste):
            _alta(cajero, placa="ABC123")

    def test_la_base_rechaza_dos_activas_aunque_el_servicio_lo_olvide(self, cajero):
        _alta(cajero)
        with pytest.raises(IntegrityError), transaction.atomic():
            Pension.objects.create(placa="ABC123", nombre_cliente="Otro")

    def test_tras_la_baja_la_placa_puede_volver(self, cajero):
        pension = _alta(cajero)
        sp.dar_de_baja(pension_id=pension.pk, motivo="Dejó de venir", operador=cajero)
        nueva = _alta(cajero, placa="ABC123")
        assert nueva.pk != pension.pk

    def test_la_baja_exige_motivo_tambien_en_la_base(self, cajero):
        pension = _alta(cajero)
        pension.estado = EstadoPension.BAJA
        pension.baja_en = timezone.now()
        with pytest.raises(IntegrityError), transaction.atomic():
            pension.save()


class TestCobro:
    def test_sin_turno_no_se_cobra(self, cajero):
        pension = _alta(cajero)
        with pytest.raises(SinTurnoAbierto):
            sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
        assert not PagoPension.objects.exists()

    def test_con_turno_de_otra_jornada_se_frena(self, cajero):
        Turno.objects.create(
            operador=cajero, abierto_en=timezone.now() - timedelta(days=2)
        )
        pension = _alta(cajero)
        with pytest.raises(SinTurnoAbierto, match="sigue abierto"):
            sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)

    def test_pago_insuficiente_no_crea_nada(self, cajero, turno):
        pension = _alta(cajero)
        with pytest.raises(PagoInsuficiente):
            sp.cobrar_pension(
                pension_id=pension.pk, dias=1, pago_recibido=Decimal("100"), operador=cajero
            )
        pension.refresh_from_db()
        assert pension.vigente_hasta is None
        assert not PagoPension.objects.exists()

    def test_el_pago_cae_en_el_turno_y_copia_el_plan(self, cajero, turno):
        pension = _alta(cajero)
        ahora = timezone.now()
        pago = sp.cobrar_pension(
            pension_id=pension.pk, dias=2, pago_recibido=Decimal("500"),
            operador=cajero, momento=ahora,
        )
        assert pago.turno == turno
        assert pago.monto == Decimal("360.00")
        assert pago.cambio == Decimal("140.00")
        assert pago.plan_nombre == "Pensión diaria"
        assert pago.periodo_hasta - pago.periodo_desde == timedelta(days=2)

    def test_una_pension_de_baja_no_se_cobra(self, cajero, turno):
        pension = _alta(cajero)
        sp.dar_de_baja(pension_id=pension.pk, motivo="Se fue", operador=cajero)
        with pytest.raises(PensionDeBaja):
            sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)


class TestEntradaYSalida:
    def _pagada(self, cajero, horas_atras=1):
        pension = _alta(cajero)
        Pension.objects.filter(pk=pension.pk).update(
            vigente_hasta=timezone.now() + timedelta(hours=24 - horas_atras)
        )
        pension.refresh_from_db()
        return pension

    def test_entrada_con_pension_vigente_es_de_pension(self, cajero):
        pension = self._pagada(cajero)
        folio = services.registrar_entrada(placa="abc123", operador=cajero)
        assert folio.pension == pension

    def test_entrada_con_pension_vencida_es_por_hora(self, cajero):
        pension = _alta(cajero)
        Pension.objects.filter(pk=pension.pk).update(
            vigente_hasta=timezone.now() - timedelta(minutes=1)
        )
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        assert folio.pension is None

    def test_la_moto_pensionada_va_a_cajon_de_moto(self, cajero):
        pension = _alta(cajero, placa="M48ZKP", tipo=TipoVehiculo.MOTOCICLETA)
        Pension.objects.filter(pk=pension.pk).update(
            vigente_hasta=timezone.now() + timedelta(hours=3)
        )
        folio = services.registrar_entrada(placa="M48ZKP", tipo_cajon="AUTO", operador=cajero)
        assert folio.tarifa.tipo_vehiculo == TipoVehiculo.MOTOCICLETA

    def test_salida_cubierta_cierra_en_cero_sin_turno_aunque_haya_uno(self, cajero, turno):
        self._pagada(cajero)
        entrada = timezone.now() - timedelta(hours=3)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero, momento=entrada)

        cerrado, cotizacion = services.registrar_salida(
            uuid_folio=folio.uuid, operador=cajero, exigir_turno=True
        )
        assert cerrado.importe == Decimal("0.00")
        assert cerrado.turno is None
        assert cerrado.estado == EstadoFolio.CERRADO
        assert cerrado.minutos == 180
        assert cerrado.desglose["regla"] == "pension"

    def test_salida_cubierta_no_exige_turno(self, cajero):
        self._pagada(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        cerrado, _ = services.registrar_salida(
            uuid_folio=folio.uuid, operador=cajero, exigir_turno=True
        )
        assert cerrado.estado == EstadoFolio.CERRADO

    def test_el_excedente_se_cobra_por_hora_y_entra_al_turno(self, cajero, turno):
        pension = _alta(cajero)
        ahora = timezone.now()
        Pension.objects.filter(pk=pension.pk).update(vigente_hasta=ahora - timedelta(minutes=90))
        folio = Folio.objects.create(
            placa="ABC123", pension=pension, entrada_en=ahora - timedelta(hours=5),
            tarifa=Tarifa.objects.get(activa=True, tipo_vehiculo=TipoVehiculo.AUTOMOVIL),
        )
        cerrado, cotizacion = services.registrar_salida(
            uuid_folio=folio.uuid, operador=cajero, pago_recibido=Decimal("50"),
            exigir_turno=True, momento=ahora,
        )
        # 90 minutos a $30/h, sin los 15 de cortesía.
        assert cerrado.importe == Decimal("45.00")
        assert cerrado.turno == turno
        assert cerrado.tarifa_minutos_cortesia == 0
        assert cerrado.desglose["minutos_excedentes"] == 90

    def test_el_excedente_sin_turno_no_se_cobra(self, cajero):
        pension = _alta(cajero)
        ahora = timezone.now()
        Pension.objects.filter(pk=pension.pk).update(vigente_hasta=ahora - timedelta(minutes=30))
        folio = Folio.objects.create(
            placa="ABC123", pension=pension, entrada_en=ahora - timedelta(hours=2),
            tarifa=Tarifa.objects.get(activa=True, tipo_vehiculo=TipoVehiculo.AUTOMOVIL),
        )
        with pytest.raises(SinTurnoAbierto):
            services.registrar_salida(uuid_folio=folio.uuid, operador=cajero, exigir_turno=True)

    def test_renovar_dentro_cubre_el_excedente(self, cajero, turno):
        """«A menos que renueve pensión»."""
        pension = _alta(cajero)
        ahora = timezone.now()
        Pension.objects.filter(pk=pension.pk).update(vigente_hasta=ahora - timedelta(hours=2))
        folio = Folio.objects.create(
            placa="ABC123", pension=pension, entrada_en=ahora - timedelta(hours=6),
            tarifa=Tarifa.objects.get(activa=True, tipo_vehiculo=TipoVehiculo.AUTOMOVIL),
        )
        pago = sp.cobrar_pension(
            pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero, momento=ahora
        )
        assert pago.periodo_desde == ahora - timedelta(hours=2)

        cerrado, _ = services.registrar_salida(
            uuid_folio=folio.uuid, operador=cajero, exigir_turno=True,
            momento=ahora + timedelta(minutes=5),
        )
        assert cerrado.importe == Decimal("0.00")

    def test_renovar_con_estancia_por_hora_la_pasa_a_pension(self, cajero, turno):
        pension = _alta(cajero)
        Pension.objects.filter(pk=pension.pk).update(
            vigente_hasta=timezone.now() - timedelta(hours=5)
        )
        entrada = timezone.now() - timedelta(hours=1)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero, momento=entrada)
        assert folio.pension is None

        pago = sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
        folio.refresh_from_db()
        assert folio.pension == pension
        assert pago.periodo_desde == entrada

        cerrado, _ = services.registrar_salida(uuid_folio=folio.uuid, operador=cajero)
        assert cerrado.importe == Decimal("0.00")

    def test_la_vigencia_se_evalua_al_instante_y_no_por_fecha(self, cajero):
        pension = _alta(cajero)
        zona = timezone.get_current_timezone()
        vence = timezone.make_aware(datetime.combine(timezone.localdate(), time(0, 0)), zona)
        Pension.objects.filter(pk=pension.pk).update(vigente_hasta=vence)

        antes = services.registrar_entrada(
            placa="ABC123", operador=cajero, momento=vence - timedelta(minutes=30)
        )
        assert antes.pension_id == pension.pk
        services.registrar_salida(uuid_folio=antes.uuid, operador=cajero,
                                  momento=vence - timedelta(minutes=20))
        despues = services.registrar_entrada(
            placa="ABC123", operador=cajero, momento=vence + timedelta(minutes=10)
        )
        assert despues.pension is None

    def test_el_patio_marca_al_pensionado(self, cajero):
        self._pagada(cajero, horas_atras=23)  # vence en una hora
        services.registrar_entrada(placa="ABC123", operador=cajero)
        fila = services.folios_en_patio()[0]
        assert fila["es_pension"]
        assert fila["vigencia"].value == "POR_VENCER"


class TestCancelacion:
    def test_solo_el_ultimo_pago_y_con_su_turno_abierto(self, cajero, turno):
        pension = _alta(cajero)
        primero = sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
        segundo = sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)

        with pytest.raises(PagoNoCancelable):
            sp.cancelar_pago(pago_id=primero.pk, motivo="Error", operador=cajero)

        sp.cancelar_pago(pago_id=segundo.pk, motivo="Se cobró dos veces", operador=cajero)
        pension.refresh_from_db()
        assert pension.vigente_hasta == primero.periodo_hasta
        segundo.refresh_from_db()
        assert segundo.estado == EstadoPagoPension.CANCELADO

    def test_no_con_el_turno_cortado(self, cajero, turno):
        pension = _alta(cajero)
        pago = sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
        services_caja.cerrar_turno(turno_id=turno.pk, efectivo_declarado=PRECIO)
        with pytest.raises(PagoNoCancelable, match="ajuste"):
            sp.cancelar_pago(pago_id=pago.pk, motivo="Error", operador=cajero)


class TestBusquedaYBitacora:
    def test_busca_sin_distinguir_mayusculas_por_placa_nombre_y_telefono(self, cajero):
        _alta(cajero)
        for texto in ("abc1", "laura", "MÉNDEZ", "5550"):
            assert len(sp.listar(texto=texto)) == 1, texto
        assert sp.listar(texto="otro") == []

    def test_la_bitacora_no_lleva_datos_personales(self, cajero, turno, caplog):
        # El registrador de la aplicación no propaga a la raíz (ver
        # `LOGGING`), así que el capturador se le cuelga directamente.
        registrador = logging.getLogger("apps.estacionamiento")
        registrador.addHandler(caplog.handler)
        nivel_anterior = registrador.level
        registrador.setLevel(logging.INFO)
        try:
            pension = _alta(cajero)
            sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
            sp.dar_de_baja(pension_id=pension.pk, motivo="Fin", operador=cajero)
        finally:
            registrador.removeHandler(caplog.handler)
            registrador.setLevel(nivel_anterior)
        assert "Laura" not in caplog.text
        assert "5555500142" not in caplog.text
        assert "ABC123" in caplog.text

"""
La pensión en el corte, los reportes, las métricas y el boleto público.

El dinero de la pensión entra a la misma caja que el de los boletos: el corte
debe esperarlo, el cierre ciego debe seguir sin revelarlo, y las salidas
cubiertas —que valen $0— no deben colarse como cortesías en ningún indicador.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import (
    services,
    services_caja,
    services_metricas,
    services_pensiones as sp,
)
from apps.estacionamiento.domain.corte import calcular_corte
from apps.estacionamiento.models import Pension, Tarifa, TipoVehiculo

pytestmark = pytest.mark.django_db


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
    return services_caja.abrir_turno(operador=cajero, fondo_inicial=Decimal("100.00"))


def _pensionado(cajero, placa="ABC123", cobrar=True):
    pension = sp.registrar_pension(
        placa=placa, tipo_vehiculo=TipoVehiculo.AUTOMOVIL, nombre_cliente="Iván Robles",
        operador=cajero,
    )
    if cobrar:
        sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)
    return pension


class TestMotorDeCorte:
    def test_sin_pensiones_el_resultado_no_cambia(self):
        antes = calcular_corte(
            fondo_inicial=Decimal("100"), importes_cobrados=[Decimal("30"), Decimal("0")],
            efectivo_declarado=Decimal("130"),
        )
        assert antes.efectivo_esperado == Decimal("130.00")
        assert antes.total_pensiones == Decimal("0.00")
        assert antes.cuadra

    def test_el_esperado_suma_boletos_y_pensiones(self):
        resumen = calcular_corte(
            fondo_inicial=Decimal("100"), importes_cobrados=[Decimal("30")],
            importes_pension=[Decimal("180")], efectivo_declarado=Decimal("310"),
        )
        assert resumen.total_cobrado == Decimal("30.00")
        assert resumen.total_pensiones == Decimal("180.00")
        assert resumen.efectivo_esperado == Decimal("310.00")
        assert resumen.pagos_pension == 1
        assert resumen.cuadra


class TestCorteDelTurno:
    def test_el_corte_cuadra_con_la_pension(self, cajero, turno):
        _pensionado(cajero)
        cerrado, resumen = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("280.00")
        )
        assert resumen.cuadra
        assert cerrado.total_pensiones == Decimal("180.00")
        assert cerrado.pagos_pension == 1

    def test_un_pago_cancelado_no_suma(self, cajero, turno):
        pension = _pensionado(cajero)
        pago = pension.pagos.get()
        sp.cancelar_pago(pago_id=pago.pk, motivo="Se devolvió el efectivo", operador=cajero)
        _, resumen = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("100.00")
        )
        assert resumen.cuadra

    def test_la_fotografia_no_cambia_con_cobros_posteriores(self, cajero, turno):
        pension = _pensionado(cajero)
        cerrado, _ = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("280.00")
        )
        services_caja.abrir_turno(operador=cajero, fondo_inicial=Decimal("0"))
        sp.cobrar_pension(pension_id=pension.pk, dias=1, pago_recibido=None, operador=cajero)

        resumen = services_caja.calcular_resumen(cerrado)
        assert resumen.total_pensiones == Decimal("180.00")
        assert resumen.efectivo_esperado == Decimal("280.00")

    def test_las_salidas_cubiertas_no_son_cortesias_del_turno(self, cajero, turno):
        _pensionado(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        services.registrar_salida(uuid_folio=folio.uuid, operador=cajero, exigir_turno=True)

        _, resumen = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("280.00")
        )
        assert resumen.folios_cortesia == 0
        assert resumen.folios_cobrados == 0

    def test_el_avance_del_turno_no_revela_el_importe(self, cajero, turno):
        _pensionado(cajero)
        avance = services_caja.resumen_operativo(turno)
        assert avance["pagos_pension"] == 1
        assert "180" not in str(avance)

    def test_el_comprobante_separa_la_pension(self, client, cajero, turno):
        _pensionado(cajero)
        services_caja.cerrar_turno(turno_id=turno.pk, efectivo_declarado=Decimal("280.00"))
        client.force_login(cajero)
        contenido = client.get(reverse("web:corte", args=[turno.pk])).content.decode()
        assert "Pensiones cobradas (1)" in contenido
        assert "$180.00" in contenido
        assert "La caja cuadra exactamente" in contenido


class TestReportesYMetricas:
    def _periodo(self):
        ahora = timezone.now()
        return ahora - timedelta(days=1), ahora + timedelta(minutes=1)

    def test_las_salidas_cubiertas_no_cuentan_como_cortesia(self, cajero, turno):
        _pensionado(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        services.registrar_salida(uuid_folio=folio.uuid, operador=cajero)

        desde, hasta = self._periodo()
        reporte = services_caja.reporte_por_rango(desde, hasta)
        assert reporte["folios_cortesia"] == 0
        assert reporte["salidas_pension"] == 1
        assert reporte["pensiones_cobradas"] == Decimal("180.00")

        metricas = services_metricas.metricas_por_rango(desde, hasta)
        etiquetas = {p["etiqueta"]: p["conteo"] for p in metricas["composicion"]["boletos"]}
        assert etiquetas["Cortesía"] == 0
        assert etiquetas["Pensión"] == 1
        assert metricas["pensiones"]["ingreso"] == Decimal("180.00")
        assert metricas["pensiones"]["activas"] == 1

    def test_frecuentes_excluye_a_quien_ya_tiene_pension(self, cajero, turno):
        ahora = timezone.now()
        for placa in ("ABC123", "XYZ987"):
            for horas in (6, 4):
                folio = services.registrar_entrada(
                    placa=placa, operador=cajero, momento=ahora - timedelta(hours=horas)
                )
                services.registrar_salida(
                    uuid_folio=folio.uuid, operador=cajero,
                    momento=ahora - timedelta(hours=horas - 1),
                )
        _pensionado(cajero, placa="ABC123", cobrar=False)

        desde, hasta = self._periodo()
        clientes = services_metricas.clientes_frecuentes(desde, hasta)
        placas = [f["placa"] for f in clientes["filas"]]
        assert placas == ["XYZ987"]

    def test_el_pdf_lleva_la_seccion_de_pensiones(self, client, cajero, turno):
        cajero.is_staff = True
        cajero.save()
        _pensionado(cajero)
        client.force_login(cajero)
        respuesta = client.get(reverse("web:metricas_pdf"))
        assert respuesta.status_code == 200
        assert respuesta["Content-Type"] == "application/pdf"


class TestBoletoPublico:
    def test_un_folio_de_pension_no_muestra_importe_ni_datos(self, client, cajero, turno):
        pension = _pensionado(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        contenido = client.get(
            reverse("boletos:publico", kwargs={"uuid_folio": folio.uuid})
        ).content.decode()

        assert "Vehículo en pensión" in contenido
        assert "Importe estimado" not in contenido
        assert "Iván" not in contenido
        pension.refresh_from_db()
        assert pension.vigente_hasta.strftime("%d/%m/%Y %H:%M") not in contenido

    def test_la_api_dice_si_es_pension_sin_datos_del_cliente(self, client, cajero, turno):
        _pensionado(cajero)
        folio = services.registrar_entrada(placa="ABC123", operador=cajero)
        client.force_login(cajero)
        datos = client.get(f"/api/v1/folios/{folio.uuid}/").json()
        assert datos["es_pension"] is True
        assert "Iván" not in str(datos)

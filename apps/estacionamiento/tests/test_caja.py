"""
Pruebas del control de caja (capa de aplicación).

Verifican la orquestación del turno: apertura con fondo, adscripción
automática de los cobros, cierre ciego y fotografía inmutable del corte.
Requieren base de datos porque ahí residen las restricciones de integridad que
se comprueban.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.estacionamiento import services, services_caja
from apps.estacionamiento.domain.exceptions import (
    ErrorConfiguracion,
    SinTurnoAbierto,
    TurnoConFoliosAbiertos,
    TurnoYaAbierto,
    TurnoYaCerrado,
)
from apps.estacionamiento.models import EstadoTurno, Tarifa, Turno

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifa():
    # Vigencia en el pasado: aplica la tarifa vigente al momento de la entrada.
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def operador():
    return get_user_model().objects.create_user(
        username="cajero", password="clave-de-prueba-1234"
    )


@pytest.fixture
def otro_operador():
    return get_user_model().objects.create_user(
        username="cajero2", password="clave-de-prueba-1234"
    )


def _cobrar(placa: str, horas: int, operador, turno=None):
    """Registra una entrada con antigüedad y la cobra de inmediato."""
    folio = services.registrar_entrada(
        placa=placa, momento=timezone.now() - timedelta(hours=horas), operador=operador
    )
    return services.registrar_salida(
        uuid_folio=folio.uuid, operador=operador, turno=turno
    )


class TestAbrirTurno:
    def test_se_abre_con_fondo_declarado(self, operador):
        turno = services_caja.abrir_turno(
            operador=operador, fondo_inicial=Decimal("500.00")
        )
        assert turno.estado == EstadoTurno.ABIERTO
        assert turno.fondo_inicial == Decimal("500.00")
        assert turno.operador == operador

    def test_no_se_abre_turno_sin_operador(self):
        # El corte se atribuye a una persona, no al sistema.
        with pytest.raises(ErrorConfiguracion):
            services_caja.abrir_turno(operador=None, fondo_inicial=Decimal("100.00"))

    def test_no_se_admite_fondo_negativo(self, operador):
        with pytest.raises(ErrorConfiguracion):
            services_caja.abrir_turno(
                operador=operador, fondo_inicial=Decimal("-50.00")
            )

    def test_un_operador_no_puede_tener_dos_turnos_abiertos(self, operador):
        services_caja.abrir_turno(operador=operador)
        with pytest.raises(TurnoYaAbierto):
            services_caja.abrir_turno(operador=operador)

    def test_dos_operadores_distintos_si_pueden_tener_turno_abierto(
        self, operador, otro_operador
    ):
        services_caja.abrir_turno(operador=operador)
        services_caja.abrir_turno(operador=otro_operador)
        assert Turno.objects.filter(estado=EstadoTurno.ABIERTO).count() == 2


class TestAdscripcionDeCobros:
    def test_el_cobro_se_adscribe_al_turno_abierto_sin_pedirlo(
        self, tarifa, operador
    ):
        turno = services_caja.abrir_turno(operador=operador)
        folio, _ = _cobrar("ABC123", 1, operador)
        # La adscripción es automática: un ingreso sin turno quedaría fuera
        # de todo corte.
        assert folio.turno == turno

    def test_sin_turno_abierto_el_folio_queda_sin_adscribir(self, tarifa, operador):
        folio, _ = _cobrar("ABC123", 1, operador)
        assert folio.turno is None

    def test_exigir_turno_interrumpe_el_cobro_sin_caja_abierta(
        self, tarifa, operador
    ):
        folio = services.registrar_entrada(placa="ABC123", operador=operador)
        with pytest.raises(SinTurnoAbierto):
            services.registrar_salida(
                uuid_folio=folio.uuid, operador=operador, exigir_turno=True
            )

    def test_los_cobros_de_otro_turno_no_contaminan_el_corte(
        self, tarifa, operador, otro_operador
    ):
        turno_a = services_caja.abrir_turno(
            operador=operador, fondo_inicial=Decimal("100.00")
        )
        turno_b = services_caja.abrir_turno(
            operador=otro_operador, fondo_inicial=Decimal("200.00")
        )
        _cobrar("AAA111", 1, operador)       # $30 -> turno A
        _cobrar("BBB222", 2, otro_operador)  # $60 -> turno B

        assert services_caja.calcular_resumen(turno_a).total_cobrado == Decimal("30.00")
        assert services_caja.calcular_resumen(turno_b).total_cobrado == Decimal("60.00")


class TestResumenOperativo:
    def test_no_revela_importes_durante_el_turno(self, tarifa, operador):
        turno = services_caja.abrir_turno(operador=operador)
        _cobrar("ABC123", 1, operador)
        resumen = services_caja.resumen_operativo(turno)
        # El cierre ciego exige que el operador no conozca el total esperado
        # antes de contar: ninguna clave monetaria debe aparecer aquí.
        assert resumen["folios_cobrados"] == 1
        assert "total_cobrado" not in resumen
        assert "efectivo_esperado" not in resumen

    def test_informa_los_vehiculos_que_siguen_dentro(self, tarifa, operador):
        turno = services_caja.abrir_turno(operador=operador)
        services.registrar_entrada(placa="DENTRO1", operador=operador)
        assert services_caja.resumen_operativo(turno)["folios_abiertos_en_patio"] == 1


class TestCerrarTurno:
    def test_cierre_que_cuadra(self, tarifa, operador):
        turno = services_caja.abrir_turno(
            operador=operador, fondo_inicial=Decimal("500.00")
        )
        _cobrar("AAA111", 1, operador)  # $30.00
        _cobrar("BBB222", 2, operador)  # $60.00

        turno, resumen = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("590.00")
        )
        assert turno.estado == EstadoTurno.CERRADO
        assert resumen.efectivo_esperado == Decimal("590.00")
        assert resumen.diferencia == Decimal("0.00")
        assert resumen.cuadra is True
        assert turno.folios_cobrados == 2

    def test_cierre_con_faltante(self, tarifa, operador):
        turno = services_caja.abrir_turno(
            operador=operador, fondo_inicial=Decimal("500.00")
        )
        _cobrar("AAA111", 1, operador)  # $30.00
        turno, resumen = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("520.00")
        )
        assert resumen.diferencia == Decimal("-10.00")
        assert resumen.hay_faltante is True
        assert turno.diferencia == Decimal("-10.00")

    def test_el_cierre_exige_conteo_fisico(self, operador):
        turno = services_caja.abrir_turno(operador=operador)
        with pytest.raises(ErrorConfiguracion):
            services_caja.cerrar_turno(turno_id=turno.pk, efectivo_declarado=None)

    def test_no_se_admite_conteo_negativo(self, operador):
        turno = services_caja.abrir_turno(operador=operador)
        with pytest.raises(ErrorConfiguracion):
            services_caja.cerrar_turno(
                turno_id=turno.pk, efectivo_declarado=Decimal("-1.00")
            )

    def test_no_se_corta_dos_veces_el_mismo_turno(self, operador):
        turno = services_caja.abrir_turno(operador=operador)
        services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("0.00")
        )
        with pytest.raises(TurnoYaCerrado):
            services_caja.cerrar_turno(
                turno_id=turno.pk, efectivo_declarado=Decimal("0.00")
            )

    def test_el_cierre_de_jornada_puede_exigir_patio_vacio(self, tarifa, operador):
        turno = services_caja.abrir_turno(operador=operador)
        services.registrar_entrada(placa="DENTRO1", operador=operador)
        with pytest.raises(TurnoConFoliosAbiertos):
            services_caja.cerrar_turno(
                turno_id=turno.pk,
                efectivo_declarado=Decimal("0.00"),
                permitir_folios_abiertos=False,
            )

    def test_tras_cerrar_el_operador_puede_abrir_un_turno_nuevo(self, operador):
        turno = services_caja.abrir_turno(operador=operador)
        services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("0.00")
        )
        nuevo = services_caja.abrir_turno(operador=operador)
        assert nuevo.pk != turno.pk


class TestInmutabilidadDelCorte:
    def test_el_corte_cerrado_no_se_recalcula(self, tarifa, operador):
        turno = services_caja.abrir_turno(
            operador=operador, fondo_inicial=Decimal("100.00")
        )
        _cobrar("AAA111", 1, operador)  # $30.00
        turno, _ = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=Decimal("130.00")
        )

        # Aunque después se altere la tarifa, el corte firmado por el operador
        # conserva sus cifras: un recálculo cambiaría una cifra ya declarada.
        tarifa.cuota_hora = Decimal("90.00")
        tarifa.save()

        resumen = services_caja.calcular_resumen(turno)
        assert resumen.total_cobrado == Decimal("30.00")
        assert resumen.diferencia == Decimal("0.00")
        assert turno.resumen["efectivo_esperado"] == "130.00"


class TestReportePorRango:
    def test_agrega_folios_importes_y_promedios(self, tarifa, operador):
        services_caja.abrir_turno(operador=operador)
        _cobrar("AAA111", 1, operador)  # $30.00, 60 min
        _cobrar("BBB222", 3, operador)  # $90.00, 180 min

        datos = services_caja.reporte_por_rango(
            timezone.now() - timedelta(days=1), timezone.now() + timedelta(minutes=1)
        )
        assert datos["folios_cobrados"] == 2
        assert datos["total_cobrado"] == Decimal("120.00")
        assert datos["importe_promedio"] == Decimal("60.00")
        assert datos["minutos_totales"] == 240

    def test_un_rango_invertido_se_rechaza(self):
        with pytest.raises(ErrorConfiguracion):
            services_caja.reporte_por_rango(
                timezone.now(), timezone.now() - timedelta(days=1)
            )

    def test_un_periodo_sin_movimientos_no_divide_entre_cero(self):
        datos = services_caja.reporte_por_rango(
            timezone.now() - timedelta(days=2), timezone.now() - timedelta(days=1)
        )
        assert datos["folios_cobrados"] == 0
        assert datos["total_cobrado"] == Decimal("0.00")
        assert datos["importe_promedio"] == Decimal("0.00")


class TestModoSinInventarioDeCajones:
    def test_sin_cajones_dados_de_alta_el_folio_se_emite_sin_cajon(
        self, tarifa, operador
    ):
        # Escenario objetivo: estacionamiento que solo controla tiempo y caja.
        folio = services.registrar_entrada(placa="ABC123", operador=operador)
        assert folio.cajon is None
        assert folio.estado == "ABIERTO"

    def test_el_ciclo_completo_funciona_sin_inventario(self, tarifa, operador):
        services_caja.abrir_turno(operador=operador, fondo_inicial=Decimal("100.00"))
        folio, cot = _cobrar("ABC123", 2, operador)
        assert folio.cajon is None
        assert cot.importe == Decimal("60.00")

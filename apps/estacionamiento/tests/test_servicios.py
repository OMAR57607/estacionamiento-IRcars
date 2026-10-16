"""
Pruebas de los casos de uso (capa de aplicación).

Verifican la orquestación: asignación de cajón, unicidad de folios abiertos,
copia de la tarifa al cierre y validación del pago. Requieren base de datos
porque es ahí donde residen las restricciones de integridad que se comprueban.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.domain.exceptions import (
    ErrorConfiguracion,
    FolioYaCerrado,
    PagoInsuficiente,
    SinCajonesDisponibles,
    VehiculoYaDentro,
)
from apps.estacionamiento.models import Cajon, EstadoFolio, Tarifa, TipoCajon

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifa():
    # La vigencia se fija en el pasado a propósito: el servicio aplica la
    # tarifa vigente **al momento de la entrada**, que es el criterio legal
    # (rige el precio exhibido cuando el cliente ingresó). Una tarifa creada
    # "ahora" no puede amparar un ingreso de hace dos horas.
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        tope_diario=Decimal("250.00"),
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajones():
    return [
        Cajon.objects.create(codigo=f"A-{n:02d}", tipo=TipoCajon.AUTOMOVIL)
        for n in range(1, 4)
    ]


class TestNormalizacionDePlaca:
    def test_se_normaliza_a_mayusculas_y_sin_espacios(self):
        assert services.normalizar_placa("  abc 123 ") == "ABC123"

    def test_se_rechaza_una_placa_vacia(self):
        with pytest.raises(ErrorConfiguracion):
            services.normalizar_placa("")

    def test_se_rechaza_una_placa_con_caracteres_invalidos(self):
        with pytest.raises(ErrorConfiguracion):
            services.normalizar_placa("ABC*123")


class TestRegistrarEntrada:
    def test_se_asigna_cajon_y_se_abre_el_folio(self, tarifa, cajones):
        folio = services.registrar_entrada(placa="abc-123")
        assert folio.estado == EstadoFolio.ABIERTO
        assert folio.placa == "ABC-123"  # normalizada
        assert folio.cajon in cajones
        assert folio.tarifa == tarifa

    def test_sin_tarifa_vigente_no_se_permite_operar(self, cajones):
        with pytest.raises(ErrorConfiguracion):
            services.registrar_entrada(placa="ABC123")

    def test_la_misma_placa_no_puede_entrar_dos_veces(self, tarifa, cajones):
        services.registrar_entrada(placa="ABC123")
        with pytest.raises(VehiculoYaDentro):
            services.registrar_entrada(placa="abc123")

    def test_se_agotan_los_cajones_del_tipo_solicitado(self, tarifa, cajones):
        for n in range(len(cajones)):
            services.registrar_entrada(placa=f"PLACA{n}")
        with pytest.raises(SinCajonesDisponibles):
            services.registrar_entrada(placa="EXTRA1")

    def test_no_se_asigna_un_cajon_de_otro_tipo(self, tarifa, cajones):
        # Solo hay cajones de automóvil: una motocicleta no debe ocuparlos.
        with pytest.raises(SinCajonesDisponibles):
            services.registrar_entrada(placa="MOTO01", tipo_cajon=TipoCajon.MOTOCICLETA)

    def test_no_se_asignan_cajones_inactivos(self, tarifa):
        Cajon.objects.create(codigo="B-01", tipo=TipoCajon.AUTOMOVIL, activo=False)
        with pytest.raises(SinCajonesDisponibles):
            services.registrar_entrada(placa="ABC123")


class TestCotizar:
    def test_la_cotizacion_no_modifica_el_folio(self, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=2)
        )
        cot = services.cotizar_folio(folio)
        assert cot.importe == Decimal("60.00")
        folio.refresh_from_db()
        assert folio.estado == EstadoFolio.ABIERTO
        assert folio.importe is None

    def test_los_segundos_no_se_cobran(self, tarifa, cajones):
        # 59 segundos son 0 minutos completos: dentro de cortesía, sin costo.
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(seconds=59)
        )
        assert services.cotizar_folio(folio).importe == Decimal("0.00")


class TestRegistrarSalida:
    def test_se_cobra_se_cierra_y_se_libera_el_cajon(self, tarifa, cajones):
        entrada = timezone.now() - timedelta(hours=1)
        folio = services.registrar_entrada(placa="ABC123", momento=entrada)
        cajon = folio.cajon

        folio, cot = services.registrar_salida(uuid_folio=folio.uuid)

        assert folio.estado == EstadoFolio.CERRADO
        assert folio.importe == Decimal("30.00")
        assert folio.minutos == 60
        assert cot.importe == Decimal("30.00")

        # El cajón queda libre: otro vehículo puede ocuparlo de inmediato.
        nuevo = services.registrar_entrada(placa="XYZ789")
        assert nuevo.cajon == cajon

    def test_la_tolerancia_por_hora_se_cobra_y_se_conserva(self, tarifa, cajones):
        """
        La regla del negocio llega hasta el cobro, no solo hasta el motor.

        Se comprueba el camino completo —tarifa vigente, entrada, salida,
        copia en el folio— porque un parámetro que el motor entiende pero que
        el servicio no copia produce un importe irreproducible: el folio
        quedaría sin poder explicar su propio cobro.
        """
        tarifa.minutos_cortesia = 0
        tarifa.minutos_tolerancia = 15
        tarifa.tope_diario = None
        tarifa.save()

        entrada = timezone.now() - timedelta(hours=1, minutes=16)
        folio = services.registrar_entrada(placa="ABC123", momento=entrada)
        folio, cot = services.registrar_salida(uuid_folio=folio.uuid)

        # 1:16 rebasa la tolerancia: dos horas a $30.
        assert cot.importe == Decimal("60.00")
        assert cot.hora_completa_aplicada is True

        folio.refresh_from_db()
        assert folio.tarifa_minutos_tolerancia == 15
        assert folio.desglose["hora_completa_aplicada"] is True

    def test_dentro_de_la_tolerancia_se_cobra_el_tiempo_real(self, tarifa, cajones):
        tarifa.minutos_cortesia = 0
        tarifa.minutos_tolerancia = 15
        tarifa.tope_diario = None
        tarifa.save()

        entrada = timezone.now() - timedelta(hours=1, minutes=10)
        folio = services.registrar_entrada(placa="XYZ789", momento=entrada)
        folio, cot = services.registrar_salida(uuid_folio=folio.uuid)

        # 1:10 no la rebasa: una hora más diez minutos.
        assert cot.importe == Decimal("35.00")
        assert cot.hora_completa_aplicada is False

    def test_se_conserva_la_tarifa_aplicada_para_auditoria(self, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=1)
        )
        folio, _ = services.registrar_salida(uuid_folio=folio.uuid)

        # La tarifa cambia después del cierre...
        tarifa.cuota_hora = Decimal("90.00")
        tarifa.save()

        folio.refresh_from_db()
        # ...pero el folio sigue explicando su propio importe con los
        # parámetros que estaban vigentes al momento del cobro.
        assert folio.tarifa_cuota_hora == Decimal("30.00")
        assert folio.importe == Decimal("30.00")
        assert folio.desglose["minutos"] == 60

    def test_el_pago_insuficiente_se_rechaza(self, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=1)
        )
        with pytest.raises(PagoInsuficiente):
            services.registrar_salida(
                uuid_folio=folio.uuid, pago_recibido=Decimal("20.00")
            )
        folio.refresh_from_db()
        # El folio permanece abierto: un cobro fallido no libera el vehículo.
        assert folio.estado == EstadoFolio.ABIERTO

    def test_se_calcula_el_cambio(self, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=1)
        )
        folio, _ = services.registrar_salida(
            uuid_folio=folio.uuid, pago_recibido=Decimal("50.00")
        )
        assert folio.importe == Decimal("30.00")
        assert folio.cambio == Decimal("20.00")

    def test_no_se_cobra_dos_veces_el_mismo_folio(self, tarifa, cajones):
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() - timedelta(hours=1)
        )
        services.registrar_salida(uuid_folio=folio.uuid)
        with pytest.raises(FolioYaCerrado):
            services.registrar_salida(uuid_folio=folio.uuid)

    def test_un_reloj_desincronizado_no_produce_un_cobro_invalido(
        self, tarifa, cajones
    ):
        # Entrada en el futuro: señal de desfase horario del equipo.
        folio = services.registrar_entrada(
            placa="ABC123", momento=timezone.now() + timedelta(hours=1)
        )
        with pytest.raises(ErrorConfiguracion):
            services.registrar_salida(uuid_folio=folio.uuid)


class TestCancelacion:
    def test_la_cancelacion_exige_motivo(self, tarifa, cajones):
        folio = services.registrar_entrada(placa="ABC123")
        with pytest.raises(ErrorConfiguracion):
            services.cancelar_folio(uuid_folio=folio.uuid, motivo="   ")

    def test_la_cancelacion_libera_el_cajon_y_queda_documentada(self, tarifa, cajones):
        folio = services.registrar_entrada(placa="ABC123")
        cajon = folio.cajon
        folio = services.cancelar_folio(
            uuid_folio=folio.uuid, motivo="Placa capturada por error"
        )
        assert folio.estado == EstadoFolio.CANCELADO
        assert "Placa capturada por error" in folio.observaciones
        assert services.registrar_entrada(placa="XYZ789").cajon == cajon


class TestOcupacion:
    def test_refleja_capacidad_y_disponibilidad(self, tarifa, cajones):
        services.registrar_entrada(placa="ABC123")
        datos = services.consultar_ocupacion()
        assert datos["capacidad_total"] == 3
        assert datos["ocupados_total"] == 1
        assert datos["disponibles_total"] == 2
        assert datos["lleno"] is False

    def test_detecta_el_estacionamiento_lleno(self, tarifa, cajones):
        for n in range(len(cajones)):
            services.registrar_entrada(placa=f"PLACA{n}")
        datos = services.consultar_ocupacion()
        assert datos["lleno"] is True
        assert datos["disponibles_total"] == 0

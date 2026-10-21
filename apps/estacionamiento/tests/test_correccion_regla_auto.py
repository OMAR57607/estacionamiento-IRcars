"""
Pruebas de la corrección de la regla del precio de automóvil.

La corrección viaja en una migración de datos, y una migración de datos es
código que toca dinero una sola vez y en producción: si se equivoca, no hay
segunda oportunidad de verla fallar en un entorno de pruebas.

Se ejercita la función de la migración directamente, con el registro de
modelos real. No es lo mismo que correr la migración —eso ya lo hace el
despliegue— pero sí verifica lo que decide: qué se conserva, qué se corrige y
qué no se toca dos veces.
"""
from datetime import timedelta
from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps as registro
from django.utils import timezone

from apps.estacionamiento.models import Tarifa, TipoVehiculo

pytestmark = pytest.mark.django_db

migracion = import_module(
    "apps.estacionamiento.migrations.0006_corrige_la_regla_del_precio_de_automovil"
)
corregir = migracion.corregir_regla_de_automovil


@pytest.fixture
def precio_mal_capturado():
    """Réplica del precio que quedó en producción: redondeo 15, sin tolerancia."""
    Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.AUTOMOVIL).delete()
    return Tarifa.objects.create(
        nombre="Precio como quedó capturado",
        tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("15.00"),
        minutos_cortesia=0,
        fraccion_minutos=15,
        minutos_tolerancia=0,
        cobro_minimo=Decimal("15.00"),
        tope_diario=Decimal("250.00"),
        activa=True,
        vigente_desde=timezone.now() - timedelta(days=5),
    )


class TestCorreccion:
    def test_corrige_la_regla_y_conserva_los_importes(self, precio_mal_capturado):
        """
        Lo que cambia es la regla de cobro. La cuota, la cortesía, el mínimo y
        el tope son decisiones de precio y esta migración no es su lugar: se
        leen del vigente y se copian tal cual.
        """
        corregir(registro, None)

        nuevo = Tarifa.objects.get(
            tipo_vehiculo=TipoVehiculo.AUTOMOVIL, activa=True
        )

        assert nuevo.fraccion_minutos == 1
        assert nuevo.minutos_tolerancia == 15
        assert nuevo.cuota_hora == precio_mal_capturado.cuota_hora
        assert nuevo.minutos_cortesia == precio_mal_capturado.minutos_cortesia
        assert nuevo.cobro_minimo == precio_mal_capturado.cobro_minimo
        assert nuevo.tope_diario == precio_mal_capturado.tope_diario

    def test_el_precio_anterior_queda_en_el_historico(self, precio_mal_capturado):
        """
        Nunca se edita en sitio: el histórico es lo que permite demostrar qué
        precio estuvo exhibido en cada fecha.
        """
        corregir(registro, None)

        precio_mal_capturado.refresh_from_db()
        assert precio_mal_capturado.activa is False
        assert precio_mal_capturado.fraccion_minutos == 15, (
            "El precio viejo conserva su regla: es lo que explica los cobros "
            "que se hicieron con él"
        )

    def test_el_cobro_pasa_a_ser_el_que_el_negocio_quiere(self, precio_mal_capturado):
        from apps.estacionamiento.domain.tarifas import cotizar

        corregir(registro, None)
        parametros = Tarifa.objects.get(
            tipo_vehiculo=TipoVehiculo.AUTOMOVIL, activa=True
        ).a_parametros()

        # 1:16 rebasa la tolerancia: dos horas completas.
        assert cotizar(76, parametros).importe == Decimal("30.00")
        # 1:10 no la rebasa: el tiempo real.
        assert cotizar(70, parametros).importe == Decimal("17.50")

    def test_no_toca_el_precio_de_motocicleta(self, precio_mal_capturado):
        antes = Tarifa.objects.get(
            tipo_vehiculo=TipoVehiculo.MOTOCICLETA, activa=True
        )

        corregir(registro, None)

        despues = Tarifa.objects.get(
            tipo_vehiculo=TipoVehiculo.MOTOCICLETA, activa=True
        )
        assert despues.pk == antes.pk

    def test_correr_dos_veces_no_crea_un_precio_de_adorno(
        self, precio_mal_capturado
    ):
        """
        Un cambio de precio sin cambio real solo ensucia el histórico que
        sirve para demostrar.
        """
        corregir(registro, None)
        corregir(registro, None)

        assert (
            Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.AUTOMOVIL).count() == 2
        )

    def test_sin_precio_de_automovil_no_inventa_ninguno(self):
        """
        La caseta lo capturará en la pantalla de Precio. Esta migración
        corrige una regla; no fija importes que nadie decidió.
        """
        Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.AUTOMOVIL).delete()

        corregir(registro, None)

        assert not Tarifa.objects.filter(
            tipo_vehiculo=TipoVehiculo.AUTOMOVIL
        ).exists()

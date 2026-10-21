"""
Pruebas del cuadro tarifario por tipo de vehículo.

Lo que se fija aquí es que **cada vehículo pague lo suyo** y que los dos
precios no se estorben. El riesgo no es que el cálculo falle —el motor es el
mismo— sino que la selección del precio se equivoque: una moto cobrada a
precio de auto no produce ningún error, solo un cobro de más que nadie nota.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.domain.exceptions import ErrorConfiguracion
from apps.estacionamiento.models import (
    Cajon,
    Tarifa,
    TipoCajon,
    TipoVehiculo,
)

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def precios():
    """
    El precio de automóvil; el de motocicleta lo siembra su migración.

    A los dos se les retrocede la vigencia, porque la tarifa que se aplica es
    la que estaba **vigente al ingresar**: un folio con entrada anterior al
    alta del precio no encontraría ninguno, que es exactamente lo que debe
    ocurrir y no lo que esta prueba examina.
    """
    Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.MOTOCICLETA).update(
        vigente_desde=timezone.now() - timedelta(days=1)
    )
    Tarifa.objects.create(
        nombre="Automóvil de prueba",
        tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("15.00"),
        minutos_cortesia=0,
        fraccion_minutos=1,
        minutos_tolerancia=15,
        cobro_minimo=Decimal("15.00"),
        tope_diario=Decimal("250.00"),
        vigente_desde=timezone.now() - timedelta(days=1),
    )
    return Tarifa.objects.filter(activa=True)


@pytest.fixture
def operador(precios):
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


class TestPrecioSembrado:
    def test_la_migracion_dejo_el_precio_de_motocicleta_operable(self):
        """
        Sin este precio, la primera moto se detendría con un error de
        configuración. El despliegue que introduce la distinción tiene que
        dejar el sistema operable.
        """
        moto = services.obtener_tarifa_vigente(
            tipo_vehiculo=TipoVehiculo.MOTOCICLETA
        )

        assert moto.cuota_hora == Decimal("10.00")
        assert moto.minutos_tolerancia == 15
        assert moto.fraccion_minutos == 1
        assert moto.minutos_cortesia == 0
        assert moto.cobro_minimo == Decimal("10.00")
        assert moto.tope_diario == Decimal("170.00")


class TestSeleccionDelPrecio:
    def test_una_moto_entra_con_el_precio_de_moto(self, operador):
        folio = services.registrar_entrada(
            placa="MOT1234", tipo_cajon=TipoCajon.MOTOCICLETA, operador=operador
        )

        assert folio.tarifa.tipo_vehiculo == TipoVehiculo.MOTOCICLETA
        assert folio.tarifa.cuota_hora == Decimal("10.00")

    def test_un_auto_entra_con_el_precio_de_auto(self, operador):
        folio = services.registrar_entrada(
            placa="ABC1234", tipo_cajon=TipoCajon.AUTOMOVIL, operador=operador
        )

        assert folio.tarifa.tipo_vehiculo == TipoVehiculo.AUTOMOVIL
        assert folio.tarifa.cuota_hora == Decimal("15.00")

    def test_un_cajon_accesible_cobra_tarifa_de_automovil(self, operador):
        """
        «Accesible» es una clase de espacio, no de vehículo.

        Quien usa un cajón accesible sigue siendo un automóvil, y preguntarle
        al tipo de cajón cuánto cobra llevaría a una pregunta sin respuesta.
        """
        Cajon.objects.create(codigo="AC-01", tipo=TipoCajon.ACCESIBLE)

        folio = services.registrar_entrada(
            placa="ACC1234", tipo_cajon=TipoCajon.ACCESIBLE, operador=operador
        )

        assert folio.tarifa.tipo_vehiculo == TipoVehiculo.AUTOMOVIL

    def test_el_cobro_de_la_moto_usa_su_propia_regla(self, operador):
        """
        Una hora y dieciséis minutos: rebasa la tolerancia y se cobran dos
        horas. A $10 la hora son $20.00, no los $30.00 de un automóvil.
        """
        folio = services.registrar_entrada(
            placa="MOT1234",
            tipo_cajon=TipoCajon.MOTOCICLETA,
            operador=operador,
            momento=timezone.now() - timedelta(hours=1, minutes=16),
        )

        cotizacion = services.cotizar_folio(folio)

        assert cotizacion.importe == Decimal("20.00")
        assert cotizacion.hora_completa_aplicada is True

    def test_sin_precio_de_moto_la_entrada_se_detiene_y_lo_dice(self, operador):
        """
        No hay respaldo silencioso al precio de automóvil.

        Cobrar una moto a precio de auto sin avisar es peor que detener esa
        entrada: el operador no tendría forma de notarlo y el cliente pagaría
        de más amparado en un precio que nunca se le exhibió.
        """
        Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.MOTOCICLETA).update(
            activa=False
        )

        with pytest.raises(ErrorConfiguracion) as excepcion:
            services.registrar_entrada(
                placa="MOT9999", tipo_cajon=TipoCajon.MOTOCICLETA, operador=operador
            )

        assert "motocicleta" in str(excepcion.value).lower()


class TestLosPreciosNoSeEstorban:
    def test_la_base_impide_dos_precios_activos_del_mismo_tipo(self, precios):
        from django.db.utils import IntegrityError

        with pytest.raises(IntegrityError):
            Tarifa.objects.create(
                nombre="Otro automóvil",
                tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
                cuota_hora=Decimal("99.00"),
                activa=True,
            )

    def test_cambiar_el_precio_del_auto_no_desactiva_el_de_la_moto(
        self, client, precios
    ):
        """
        El desvío que más daño haría: cambiar un precio y dejar al otro tipo
        sin ninguno vigente, de modo que la siguiente moto no pudiera entrar.
        """
        duena = get_user_model().objects.create_user(
            username="duena", password=CLAVE, is_staff=True
        )
        client.force_login(duena)
        client.post(
            reverse("web:tarifa_cambiar"),
            {"accion": "desbloquear", "clave": CLAVE},
        )

        respuesta = client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "tipo": TipoVehiculo.AUTOMOVIL,
                "cuota_hora": "18.00",
                "minutos_cortesia": "0",
                "fraccion_minutos": "1",
                "minutos_tolerancia": "15",
                "cobro_minimo": "18.00",
            },
        )

        assert respuesta.status_code == 302
        moto = services.obtener_tarifa_vigente(
            tipo_vehiculo=TipoVehiculo.MOTOCICLETA
        )
        assert moto.cuota_hora == Decimal("10.00")
        auto = services.obtener_tarifa_vigente()
        assert auto.cuota_hora == Decimal("18.00")

    def test_el_cambio_se_aplica_al_tipo_pedido(self, client, precios):
        duena = get_user_model().objects.create_user(
            username="duena", password=CLAVE, is_staff=True
        )
        client.force_login(duena)
        client.post(
            reverse("web:tarifa_cambiar"),
            {"accion": "desbloquear", "clave": CLAVE},
        )

        client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "tipo": TipoVehiculo.MOTOCICLETA,
                "cuota_hora": "12.00",
                "minutos_cortesia": "0",
                "fraccion_minutos": "1",
                "minutos_tolerancia": "15",
                "cobro_minimo": "12.00",
            },
        )

        assert services.obtener_tarifa_vigente(
            tipo_vehiculo=TipoVehiculo.MOTOCICLETA
        ).cuota_hora == Decimal("12.00")
        assert services.obtener_tarifa_vigente().cuota_hora == Decimal("15.00")


class TestPantalla:
    def test_la_pantalla_muestra_los_dos_precios(self, client, operador):
        client.force_login(operador)

        contenido = client.get(reverse("web:tarifa")).content.decode()

        assert "Automóvil" in contenido
        assert "Motocicleta" in contenido
        assert "$15.00" in contenido
        assert "$10.00" in contenido

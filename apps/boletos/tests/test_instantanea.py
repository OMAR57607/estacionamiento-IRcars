"""
Pruebas del contrato de publicación.

Lo decisivo aquí no es lo que la instantánea contiene, sino **lo que no
contiene**. Una fuga de datos en este punto se propagaría a la nube y a la
dirección pública sin que nada más la detenga.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.boletos.dominio import RELLENO
from apps.boletos.instantanea import VERSION_CONTRATO, instantanea_publica
from apps.estacionamiento import services

pytestmark = pytest.mark.django_db


# Campos del folio que jamás deben viajar al servicio público.
PROHIBIDOS = (
    "placa",
    "operador_entrada",
    "operador_salida",
    "operador_entrada_id",
    "operador_salida_id",
    "turno",
    "turno_id",
    "pago_recibido",
    "cambio",
    "observaciones",
    "desglose",
    "id",
)


class TestMinimizacion:
    def test_no_viaja_la_placa_completa(self, folio_abierto):
        carga = instantanea_publica(folio_abierto)
        valores = " ".join(str(v) for v in carga.values())
        assert "ABC1234" not in valores
        assert carga["placa_enmascarada"] == f"ABC{RELLENO * 4}"

    @pytest.mark.parametrize("campo", PROHIBIDOS)
    def test_no_viajan_los_datos_internos(self, folio_abierto, campo):
        """
        El servicio público no puede filtrar lo que nunca recibió.

        Se enumeran uno por uno para que, si alguien añade un campo al
        contrato, la prueba señale exactamente cuál.
        """
        assert campo not in instantanea_publica(folio_abierto)

    def test_no_viaja_el_identificador_secuencial(self, folio_abierto):
        """
        Solo el UUID identifica al boleto.

        Publicar el `id` autoincremental permitiría recorrer los boletos del
        día a partir de uno solo, que es el fallo de control de acceso que el
        UUID existe para evitar. Se comprueba que ninguna clave del contrato
        transporte ese entero y que el identificador publicado sea un UUID
        versión 4 —aleatorio, no derivado del `pk`—.
        """
        import uuid as modulo_uuid

        carga = instantanea_publica(folio_abierto)

        assert "id" not in carga and "pk" not in carga
        assert carga["uuid_folio"] == str(folio_abierto.uuid)

        identificador = modulo_uuid.UUID(carga["uuid_folio"])
        assert identificador.version == 4, (
            "El identificador público debe ser aleatorio: un UUID derivado de "
            "datos del folio sería predecible."
        )


class TestTarifaExhibida:
    def test_folio_abierto_usa_la_tarifa_del_ingreso(self, folio_abierto, tarifa):
        carga = instantanea_publica(folio_abierto)
        assert carga["cuota_hora"] == "30.00"
        assert carga["minutos_cortesia"] == 15
        assert carga["fraccion_minutos"] == 1

    def test_un_cambio_de_precio_posterior_no_altera_el_boleto(
        self, folio_abierto, tarifa
    ):
        """
        El precio publicado es el exhibido al entrar, no el vigente ahora.

        Es la obligación que importa ante PROFECO: al cliente se le aplica el
        precio que vio al ingresar. El cuadro tarifario es inmutable —un
        cambio crea una tarifa nueva y desactiva la anterior—, y esta prueba
        fija ese comportamiento para que no se rompa con un `update` cómodo.
        """
        from apps.estacionamiento.models import Tarifa

        Tarifa.objects.filter(activa=True).update(activa=False)
        Tarifa.objects.create(
            nombre="Precio nuevo, más caro",
            cuota_hora=Decimal("90.00"),
            minutos_cortesia=0,
            fraccion_minutos=1,
            activa=True,
            vigente_desde=timezone.now(),
        )

        folio_abierto.refresh_from_db()
        carga = instantanea_publica(folio_abierto)
        assert carga["cuota_hora"] == "30.00", (
            "El boleto debe seguir mostrando el precio exhibido al ingresar."
        )

    def test_folio_cerrado_usa_la_copia_grabada_al_cobrar(
        self, folio_abierto, operador, tarifa
    ):
        """
        En un folio liquidado manda la copia del folio, no la tarifa vigente.

        Esa copia es la que justifica el importe cobrado y la que debe poder
        exhibirse ante una aclaración.
        """
        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        carga = instantanea_publica(folio)

        assert carga["estado"] == "CERRADO"
        assert carga["cuota_hora"] == str(folio.tarifa_cuota_hora)
        assert carga["importe"] == str(folio.importe)
        assert carga["salida_en"] is not None
        assert carga["minutos"] == folio.minutos


class TestFormato:
    def test_los_importes_viajan_como_texto(self, folio_abierto):
        """
        Nunca como número JSON: sería un flotante y perdería exactitud.

        Un importe que no se reconstruye idéntico en el otro extremo rompería
        la reproducibilidad del cálculo, que es el punto de partida de todo el
        motor de tarifas.
        """
        carga = instantanea_publica(folio_abierto)
        assert isinstance(carga["cuota_hora"], str)
        assert isinstance(carga["cobro_minimo"], str)

    def test_declara_la_version_del_contrato(self, folio_abierto):
        assert instantanea_publica(folio_abierto)["version"] == VERSION_CONTRATO

    def test_es_serializable_a_json(self, folio_abierto):
        """
        Debe poder guardarse tal cual en la bandeja y enviarse sin conversión.

        Un `Decimal` o un `datetime` sin convertir reventaría al serializar,
        pero solo en el momento del envío —fuera del camino de la caja—, lo
        que convertiría el fallo en un boleto que nunca aparece sin error
        visible en pantalla.
        """
        import json

        assert json.loads(json.dumps(instantanea_publica(folio_abierto)))

    def test_folio_sin_cajon_publica_ubicacion_vacia(self, tarifa, operador):
        """Un predio que no inventaría cajones no debe fallar al publicar."""
        folio = services.registrar_entrada(placa="XYZ9876", operador=operador)
        assert instantanea_publica(folio)["ubicacion"] == ""

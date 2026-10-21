"""
Pruebas del turno que nadie cerró.

Es el fallo que reportó quien usa la caseta: el turno se queda abierto y nadie
se entera hasta que el corte del día siguiente sale incomprensible. Lo que se
fija aquí es que el sistema deje de depender de la memoria del operador.

Dos reglas con efectos distintos, y la diferencia importa:

* **Turno largo del mismo día**: se avisa, no se frena. Doce horas de caseta
  pueden ser legítimas y un vehículo en la rampa no espera a que alguien
  cuadre la caja.
* **Turno de otra jornada**: se frena el cobro. Un cobro que cae en el turno
  de ayer mezcla el efectivo de dos días y deja la diferencia sin lectura.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja
from apps.estacionamiento.models import Cajon, EstadoFolio, Folio, Tarifa, TipoCajon

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def operador():
    Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=0,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )
    Cajon.objects.create(codigo="A-01", tipo=TipoCajon.AUTOMOVIL)
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


def _abrir_turno_hace(operador, **delta):
    turno = services_caja.abrir_turno(
        operador=operador, fondo_inicial=Decimal("500.00")
    )
    turno.abierto_en = timezone.now() - timedelta(**delta)
    turno.save(update_fields=["abierto_en"])
    return turno


class TestEstadoDelTurno:
    def test_un_turno_recien_abierto_no_es_largo(self, operador):
        turno = _abrir_turno_hace(operador, minutes=30)

        estado = services_caja.estado_del_turno(turno)

        assert estado["es_largo"] is False
        assert estado["de_otro_dia"] is False

    def test_doce_horas_ya_es_largo(self, operador):
        turno = _abrir_turno_hace(operador, hours=12, minutes=5)

        estado = services_caja.estado_del_turno(turno)

        assert estado["es_largo"] is True
        assert estado["horas_enteras"] == 12

    def test_la_jornada_se_mide_por_fecha_local_y_no_por_horas(self, operador):
        """
        Un turno abierto a las 23:50 y revisado a las 00:10 lleva veinte
        minutos, pero ya es de otra jornada de caja.

        Medirlo en horas transcurridas dejaría pasar justo el caso que más
        daño hace: el turno que cruza la medianoche.
        """
        zona = timezone.get_current_timezone()
        anoche = timezone.localtime(timezone.now()).replace(
            hour=23, minute=50, second=0, microsecond=0
        ) - timedelta(days=1)
        turno = _abrir_turno_hace(operador, minutes=1)
        turno.abierto_en = anoche.astimezone(zona)
        turno.save(update_fields=["abierto_en"])

        madrugada = (anoche + timedelta(minutes=20)).astimezone(zona)
        estado = services_caja.estado_del_turno(turno, ahora=madrugada)

        assert estado["horas"] < 1
        assert estado["de_otro_dia"] is True
        assert estado["es_largo"] is True


class TestAvisoEnLaCaja:
    def test_la_caja_avisa_del_turno_largo(self, client, operador):
        _abrir_turno_hace(operador, hours=13)
        client.force_login(operador)

        contenido = client.get(reverse("web:caja")).content.decode()

        assert "con el turno abierto" in contenido
        assert reverse("web:turno") in contenido

    def test_la_caja_no_molesta_con_un_turno_normal(self, client, operador):
        _abrir_turno_hace(operador, hours=2)
        client.force_login(operador)

        contenido = client.get(reverse("web:caja")).content.decode()

        assert "con el turno abierto" not in contenido

    def test_el_turno_de_ayer_se_avisa_como_error_y_no_como_sugerencia(
        self, client, operador
    ):
        _abrir_turno_hace(operador, days=1, hours=3)
        client.force_login(operador)

        contenido = client.get(reverse("web:caja")).content.decode()

        assert "sigue abierto desde el" in contenido
        assert "aviso error" in contenido


class TestFrenoAlCobro:
    def _folio_por_cobrar(self, operador):
        return services.registrar_entrada(
            placa="ABC1234",
            operador=operador,
            momento=timezone.now() - timedelta(hours=2),
        )

    def test_no_se_cobra_contra_el_turno_de_ayer(self, client, operador):
        _abrir_turno_hace(operador, days=1, hours=2)
        folio = self._folio_por_cobrar(operador)
        client.force_login(operador)

        respuesta = client.post(
            reverse("web:caja"),
            {
                "accion": "cobrar",
                "folio": str(folio.uuid),
                "pago_recibido": "100.00",
            },
        )

        assert respuesta["Location"] == reverse("web:turno")
        folio.refresh_from_db()
        assert folio.estado == EstadoFolio.ABIERTO, "El cobro no debió ocurrir"

    def test_las_entradas_siguen_funcionando_con_el_turno_de_ayer(
        self, client, operador
    ):
        """
        El freno es al cobro, no a la operación entera.

        Un vehículo en la rampa no puede esperar a que alguien cuadre la caja,
        y una entrada no mueve efectivo: no ensucia ningún corte.
        """
        _abrir_turno_hace(operador, days=1, hours=2)
        client.force_login(operador)

        client.post(reverse("web:caja"), {"accion": "entrada", "placa": "XYZ9876"})

        assert Folio.objects.filter(placa="XYZ9876").exists()

    def test_un_turno_largo_del_mismo_dia_no_frena_el_cobro(self, client, operador):
        """
        Avisar no es estorbar. Doce horas de caseta pueden ser legítimas.
        """
        _abrir_turno_hace(operador, hours=13)
        folio = self._folio_por_cobrar(operador)
        client.force_login(operador)

        client.post(
            reverse("web:caja"),
            {
                "accion": "cobrar",
                "folio": str(folio.uuid),
                "pago_recibido": "100.00",
            },
        )

        folio.refresh_from_db()
        assert folio.estado == EstadoFolio.CERRADO

    def test_tras_cerrar_y_abrir_de_nuevo_se_puede_cobrar(self, client, operador):
        """
        El freno tiene salida, y es la que se quiere: cerrar el turno viejo.
        """
        turno_viejo = _abrir_turno_hace(operador, days=1, hours=2)
        folio = self._folio_por_cobrar(operador)
        client.force_login(operador)

        services_caja.cerrar_turno(
            turno_id=turno_viejo.pk, efectivo_declarado=Decimal("500.00")
        )
        services_caja.abrir_turno(operador=operador, fondo_inicial=Decimal("500.00"))

        client.post(
            reverse("web:caja"),
            {
                "accion": "cobrar",
                "folio": str(folio.uuid),
                "pago_recibido": "100.00",
            },
        )

        folio.refresh_from_db()
        assert folio.estado == EstadoFolio.CERRADO

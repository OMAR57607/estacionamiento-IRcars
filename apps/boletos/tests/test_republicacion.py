"""
Pruebas del comando que reconstruye el servicio de boletos.

El escenario que cubren es el que motivó el comando: la bandeja de salida es
una cola de trabajo, no un historial, así que una vez entregada la publicación
su fila desaparece y **nada reenvía lo ya entregado**. Para reproducirlo, las
pruebas vacían la bandeja antes de republicar —que es exactamente lo que deja
una entrega exitosa—.
"""
import urllib.error
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.boletos.models import PublicacionPendiente
from apps.estacionamiento import services
from apps.estacionamiento.models import EstadoFolio, Folio

pytestmark = pytest.mark.django_db


@pytest.fixture
def bandeja_entregada():
    """
    Deja la bandeja como la deja una entrega exitosa: vacía.

    Es la condición de partida del problema. Sin vaciarla, las pruebas
    medirían el encolado de la señal `post_save` y no la republicación.
    """

    def _vaciar():
        PublicacionPendiente.objects.all().delete()

    return _vaciar


@pytest.fixture
def red_ok(monkeypatch):
    class _Respuesta:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def _acepta(_peticion, timeout=None):
        return _Respuesta()

    monkeypatch.setattr("urllib.request.urlopen", _acepta)


def _cerrar(folio, operador):
    folio_cerrado, _ = services.registrar_salida(
        uuid_folio=folio.uuid,
        pago_recibido=Decimal("500.00"),
        operador=operador,
    )
    return folio_cerrado


def _envejecer_folio(folio, horas: int) -> None:
    """Antigua el folio sin pasar por `save`, que reiniciaría `actualizado_en`."""
    Folio.objects.filter(pk=folio.pk).update(
        actualizado_en=timezone.now() - timedelta(hours=horas)
    )


class TestRequisitos:
    def test_sin_publicacion_configurada_falla_con_un_mensaje_util(
        self, servicio_configurado, folio_abierto
    ):
        servicio_configurado.IRCARS_PUBLICAR_BOLETOS = False
        with pytest.raises(CommandError, match="desactivada"):
            call_command("republicar_boletos", stdout=StringIO())


class TestFoliosAbiertos:
    def test_republica_el_vehiculo_que_sigue_dentro(
        self, publicacion_encendida, folio_abierto, bandeja_entregada
    ):
        """
        Es el caso que motiva el comando.

        Tras encender el servicio o recrear su base, el auto que ya está
        dentro no tiene boleto publicado y su dueño vería «boleto no
        disponible» justo cuando va a salir.
        """
        bandeja_entregada()
        assert not PublicacionPendiente.objects.exists()

        call_command("republicar_boletos", stdout=StringIO())

        pendiente = PublicacionPendiente.objects.get()
        assert pendiente.uuid_folio == folio_abierto.uuid
        assert pendiente.cuerpo["estado"] == EstadoFolio.ABIERTO

    def test_republica_con_la_placa_enmascarada(
        self, publicacion_encendida, folio_abierto, bandeja_entregada
    ):
        """
        La republicación usa la misma instantánea que la publicación normal.

        Si construyera la carga por su cuenta, podría saltarse el enmascarado
        y filtrar la placa completa precisamente en una operación masiva.
        """
        bandeja_entregada()
        call_command("republicar_boletos", stdout=StringIO())

        cuerpo = PublicacionPendiente.objects.get().cuerpo
        assert cuerpo["placa_enmascarada"] == "ABC••••"
        assert "ABC1234" not in str(cuerpo.values())

    def test_es_idempotente(
        self, publicacion_encendida, folio_abierto, bandeja_entregada
    ):
        """Ejecutarlo dos veces no duplica trabajo ni filas."""
        bandeja_entregada()
        call_command("republicar_boletos", stdout=StringIO())
        call_command("republicar_boletos", stdout=StringIO())
        assert PublicacionPendiente.objects.count() == 1

    def test_informa_cuantos_reencolo(
        self, publicacion_encendida, tarifa, cajones, operador, bandeja_entregada
    ):
        services.registrar_entrada(placa="AAA1111", operador=operador)
        services.registrar_entrada(placa="BBB2222", operador=operador)
        bandeja_entregada()

        salida = StringIO()
        call_command("republicar_boletos", stdout=salida)

        assert "Reencolados: 2" in salida.getvalue()
        assert PublicacionPendiente.objects.count() == 2


class TestFoliosCerrados:
    def test_por_omision_no_toca_los_cerrados(
        self, publicacion_encendida, folio_abierto, operador, bandeja_entregada
    ):
        """
        Un folio liquidado ya cumplió su función; el auto se fue.

        Reencolarlo por omisión sería trabajo y tráfico de más en el caso
        habitual, que es recuperar a los que siguen dentro.
        """
        _cerrar(folio_abierto, operador)
        bandeja_entregada()

        call_command("republicar_boletos", stdout=StringIO())
        assert not PublicacionPendiente.objects.exists()

    def test_incluye_los_cerrados_vigentes_cuando_se_pide(
        self, publicacion_encendida, folio_abierto, operador, bandeja_entregada
    ):
        """
        El cliente que acaba de pagar puede querer ver su boleto liquidado.
        """
        _cerrar(folio_abierto, operador)
        bandeja_entregada()

        call_command("republicar_boletos", "--incluir-cerrados", stdout=StringIO())

        pendiente = PublicacionPendiente.objects.get()
        assert pendiente.cuerpo["estado"] == EstadoFolio.CERRADO

    def test_excluye_los_cerrados_cuya_pagina_ya_vencio(
        self, publicacion_encendida, folio_abierto, operador, bandeja_entregada
    ):
        """
        Republicar un boleto vencido es trabajo inútil.

        La vista pública lo contestaría con un 404 en cuanto llegara, porque
        su vigencia ya pasó. La ventana por omisión del comando es esa misma
        vigencia, de modo que las dos reglas no puedan desalinearse.
        """
        cerrado = _cerrar(folio_abierto, operador)
        vigencia = publicacion_encendida.IRCARS_HORAS_VIGENCIA_BOLETO
        _envejecer_folio(cerrado, horas=vigencia + 1)
        bandeja_entregada()

        call_command("republicar_boletos", "--incluir-cerrados", stdout=StringIO())
        assert not PublicacionPendiente.objects.exists()

    def test_incluye_los_cancelados_recientes(
        self, publicacion_encendida, folio_abierto, operador, bandeja_entregada
    ):
        """
        Caso que un filtro por `salida_en` dejaría fuera para siempre.

        Una cancelación marca el folio como `CANCELADO` pero **no** registra
        hora de salida, porque el vehículo no salió por caja. Por eso la
        selección filtra por la fecha del último cambio.
        """
        services.cancelar_folio(
            uuid_folio=folio_abierto.uuid,
            motivo="placa mal capturada en la prueba",
            operador=operador,
        )
        bandeja_entregada()

        call_command("republicar_boletos", "--incluir-cerrados", stdout=StringIO())

        pendiente = PublicacionPendiente.objects.get()
        assert pendiente.cuerpo["estado"] == EstadoFolio.CANCELADO
        assert pendiente.cuerpo["salida_en"] is None

    def test_la_ventana_se_puede_ampliar(
        self, publicacion_encendida, folio_abierto, operador, bandeja_entregada
    ):
        """`--horas` permite recuperar más historia en una migración."""
        cerrado = _cerrar(folio_abierto, operador)
        _envejecer_folio(cerrado, horas=48)
        bandeja_entregada()

        call_command(
            "republicar_boletos", "--incluir-cerrados", "--horas", "72",
            stdout=StringIO(),
        )
        assert PublicacionPendiente.objects.count() == 1


class TestSimulacion:
    def test_no_toca_la_bandeja(
        self, publicacion_encendida, folio_abierto, bandeja_entregada
    ):
        """Permite comprobar el alcance antes de mover nada."""
        bandeja_entregada()

        salida = StringIO()
        call_command("republicar_boletos", "--simular", stdout=salida)

        assert "Folios a republicar: 1" in salida.getvalue()
        assert str(folio_abierto.uuid) in salida.getvalue()
        assert not PublicacionPendiente.objects.exists()


class TestEnvioInmediato:
    def test_publicar_entrega_en_la_misma_ejecucion(
        self, publicacion_encendida, folio_abierto, bandeja_entregada, red_ok
    ):
        """
        `--publicar` evita esperar al hilo de fondo.

        Es lo que se quiere al reconstruir el servicio: comprobar en el acto
        que los boletos llegaron, no averiguarlo veinte segundos después.
        """
        bandeja_entregada()

        salida = StringIO()
        call_command("republicar_boletos", "--publicar", stdout=salida)

        # Se comprueba el resultado del envío y que la bandeja quede vacía.
        # No se examina la tabla del servicio: el transporte está simulado y
        # la carga no llega a la vista de ingesta. El recorrido completo
        # —bandeja, HTTP, ingesta, boleto publicado— está cubierto en
        # `test_ingesta.py`, y repetirlo aquí con un transporte falso daría
        # una garantía que esta prueba no puede ofrecer.
        assert "Publicados: 1" in salida.getvalue()
        assert not PublicacionPendiente.objects.exists()

    def test_un_fallo_de_red_conserva_lo_reencolado(
        self, publicacion_encendida, folio_abierto, bandeja_entregada, monkeypatch
    ):
        """
        Si la red falla durante la republicación, nada se pierde.

        Lo reencolado queda en la bandeja y el hilo de la caja lo reintentará,
        que es la misma garantía que rige la publicación normal.
        """

        def _falla(*_args, **_kwargs):
            raise urllib.error.URLError("red no disponible")

        monkeypatch.setattr("urllib.request.urlopen", _falla)
        bandeja_entregada()

        salida = StringIO()
        call_command("republicar_boletos", "--publicar", stdout=salida)

        assert "Con fallo" in salida.getvalue()
        assert PublicacionPendiente.objects.count() == 1

        # Reprogramada, no descartada: el hilo de la caja la reintentará.
        assert PublicacionPendiente.objects.get().intentos == 1

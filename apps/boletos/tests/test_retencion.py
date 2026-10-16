"""
Pruebas de la caducidad y la depuración del servicio público.

Cubren un caso que es fácil pasar por alto y que estuvo mal en la primera
versión de este módulo: **un folio cancelado no tiene hora de salida**, porque
el vehículo no salió por caja. Cualquier regla basada únicamente en `salida_en`
deja los boletos cancelados consultables para siempre y fuera del alcance de
la depuración.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.boletos.management.commands.depurar_boletos import DIAS_RETENCION
from apps.boletos.models import BoletoPublicado, EstadoBoleto

pytestmark = pytest.mark.django_db


def ruta_de(uuid_folio) -> str:
    return reverse("boletos:publico", kwargs={"uuid_folio": uuid_folio})


def _envejecer(boleto: BoletoPublicado, dias: int) -> None:
    """
    Antigua el boleto sin pasar por `save`.

    `actualizado_en` lleva `auto_now`, de modo que cualquier `save()` lo
    devolvería al presente y la prueba no podría examinar la retención.
    """
    momento = timezone.now() - timedelta(days=dias)
    BoletoPublicado.objects.filter(pk=boleto.pk).update(actualizado_en=momento)
    boleto.refresh_from_db()


class TestFinDeCiclo:
    def test_un_boleto_abierto_no_tiene_fin_de_ciclo(self, folio_abierto, publicar):
        assert publicar(folio_abierto).fin_de_ciclo is None

    def test_un_boleto_liquidado_usa_su_hora_de_salida(
        self, folio_abierto, operador, publicar
    ):
        from apps.estacionamiento import services

        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        boleto = publicar(folio)
        assert boleto.fin_de_ciclo == boleto.salida_en

    def test_un_boleto_cancelado_cae_en_la_fecha_del_ultimo_cambio(
        self, folio_abierto, publicar
    ):
        """
        Sin este respaldo, un cancelado quedaría sin fin de ciclo para siempre.
        """
        boleto = publicar(folio_abierto)
        boleto.estado = EstadoBoleto.CANCELADO
        boleto.save(update_fields=["estado"])

        assert boleto.salida_en is None
        assert boleto.fin_de_ciclo == boleto.actualizado_en


class TestCaducidadPublica:
    def test_un_cancelado_antiguo_deja_de_consultarse(
        self, client, folio_abierto, publicar, servicio_configurado
    ):
        """
        Caso que la primera versión no cubría: el cancelado no caducaba nunca.

        Su página pública seguía respondiendo indefinidamente porque la regla
        miraba `salida_en`, que en una cancelación es nulo.
        """
        boleto = publicar(folio_abierto)
        boleto.estado = EstadoBoleto.CANCELADO
        boleto.save(update_fields=["estado"])
        _envejecer(boleto, dias=3)

        respuesta = client.get(ruta_de(boleto.uuid_folio), secure=True)
        assert respuesta.status_code == 404

    def test_un_cancelado_reciente_sigue_consultandose(
        self, client, folio_abierto, publicar, servicio_configurado
    ):
        """
        El cliente debe poder ver que su boleto fue cancelado.

        Es información que le concierne: explica por qué no se le cobró.
        """
        boleto = publicar(folio_abierto)
        boleto.estado = EstadoBoleto.CANCELADO
        boleto.save(update_fields=["estado"])

        assert client.get(ruta_de(boleto.uuid_folio), secure=True).status_code == 200


class TestComandoDepurar:
    def test_depura_los_liquidados_antiguos(
        self, folio_abierto, operador, publicar, servicio_configurado
    ):
        from apps.estacionamiento import services

        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        boleto = publicar(folio)
        BoletoPublicado.objects.filter(pk=boleto.pk).update(
            salida_en=timezone.now() - timedelta(days=DIAS_RETENCION + 1)
        )

        call_command("depurar_boletos", stdout=StringIO())
        assert not BoletoPublicado.objects.exists()

    def test_depura_tambien_los_cancelados_antiguos(
        self, folio_abierto, publicar, servicio_configurado
    ):
        """
        Sin la segunda rama del filtro, estos se acumularían indefinidamente.
        """
        boleto = publicar(folio_abierto)
        boleto.estado = EstadoBoleto.CANCELADO
        boleto.save(update_fields=["estado"])
        _envejecer(boleto, dias=DIAS_RETENCION + 1)

        call_command("depurar_boletos", stdout=StringIO())
        assert not BoletoPublicado.objects.exists()

    def test_nunca_depura_un_boleto_abierto(
        self, folio_abierto, publicar, servicio_configurado
    ):
        """
        Por antiguo que sea, el vehículo podría seguir dentro.

        Borrarlo dejaría al cliente sin poder consultar su propio boleto, que
        es exactamente el servicio que se le ofreció al entrar.
        """
        boleto = publicar(folio_abierto)
        _envejecer(boleto, dias=365)
        BoletoPublicado.objects.filter(pk=boleto.pk).update(
            entrada_en=timezone.now() - timedelta(days=365)
        )

        call_command("depurar_boletos", stdout=StringIO())
        assert BoletoPublicado.objects.filter(pk=boleto.pk).exists()

    def test_no_depura_lo_reciente(
        self, folio_abierto, operador, publicar, servicio_configurado
    ):
        from apps.estacionamiento import services

        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        publicar(folio)

        call_command("depurar_boletos", stdout=StringIO())
        assert BoletoPublicado.objects.count() == 1

    def test_el_modo_simulacion_no_borra(
        self, folio_abierto, publicar, servicio_configurado
    ):
        """Permite comprobar el alcance antes de ejecutar un borrado real."""
        boleto = publicar(folio_abierto)
        boleto.estado = EstadoBoleto.CANCELADO
        boleto.save(update_fields=["estado"])
        _envejecer(boleto, dias=DIAS_RETENCION + 1)

        salida = StringIO()
        call_command("depurar_boletos", "--simular", stdout=salida)

        assert "1" in salida.getvalue()
        assert BoletoPublicado.objects.count() == 1


class TestComandoPublicar:
    def test_listar_no_exige_configuracion(self, publicacion_encendida, folio_abierto):
        """
        El diagnóstico debe funcionar incluso con el servicio mal configurado.

        Es justo cuando más se necesita: si exigiera configuración válida, no
        serviría para averiguar qué está mal.
        """
        salida = StringIO()
        call_command("publicar_boletos", "--solo-listar", stdout=salida)
        assert str(folio_abierto.uuid) in salida.getvalue()

    def test_sin_configuracion_el_envio_falla_con_un_mensaje_util(
        self, servicio_configurado, folio_abierto
    ):
        from django.core.management.base import CommandError

        servicio_configurado.IRCARS_PUBLICAR_BOLETOS = False
        with pytest.raises(CommandError, match="desactivada"):
            call_command("publicar_boletos", stdout=StringIO())

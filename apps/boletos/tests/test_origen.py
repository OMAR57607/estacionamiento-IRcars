"""
Pruebas del origen desde el que se sirve el boleto.

El mismo sistema se despliega de dos formas y la vista pública debe funcionar
en ambas sin una bandera de configuración que haya que acertar:

* **Aplicación web completa**: la caja vive aquí, existe el folio, y el boleto
  se construye leyéndolo y enmascarando al presentar.
* **Servicio de solo lectura**: no hay folios, y responde la copia publicada,
  que llegó ya enmascarada desde una caja local.

Lo que estas pruebas protegen es que el enmascarado se aplique **en los dos
caminos**. Es el punto donde una regresión sería más grave y menos visible: la
página seguiría funcionando, solo que mostrando la placa completa.
"""
import uuid as modulo_uuid

import pytest
from django.urls import reverse

from apps.boletos.models import BoletoPublicado
from apps.boletos.presentacion import desde_folio, desde_publicado
from apps.estacionamiento.models import Folio

pytestmark = pytest.mark.django_db


def ruta_de(uuid_folio) -> str:
    return reverse("boletos:publico", kwargs={"uuid_folio": uuid_folio})


class TestDesdeElFolio:
    def test_sirve_el_boleto_sin_ninguna_copia_publicada(
        self, client, folio_abierto, servicio_configurado
    ):
        """
        Es la arquitectura de la aplicación web: no hay nada que publicar.

        Antes, la vista solo sabía leer copias publicadas. Con la caja en el
        mismo servicio, esas copias no existen y el boleto respondería 404 con
        el folio perfectamente guardado.
        """
        assert not BoletoPublicado.objects.exists()

        respuesta = client.get(ruta_de(folio_abierto.uuid), secure=True)
        assert respuesta.status_code == 200

    def test_enmascara_la_placa_al_presentar(
        self, client, folio_abierto, servicio_configurado
    ):
        """
        El folio guarda la placa completa; la página no debe mostrarla.

        En este camino el enmascarado ocurre al presentar, no al publicar, y
        es la única barrera entre el dato y el navegador del cliente.
        """
        assert folio_abierto.placa == "ABC1234"

        contenido = client.get(
            ruta_de(folio_abierto.uuid), secure=True
        ).content.decode()
        assert "ABC1234" not in contenido
        assert "ABC••••" in contenido

    def test_muestra_el_importe_acumulado(
        self, client, folio_abierto, servicio_configurado
    ):
        from apps.estacionamiento import services

        cotizacion = services.cotizar_folio(folio_abierto)
        contenido = client.get(
            ruta_de(folio_abierto.uuid), secure=True
        ).content.decode()
        assert f"${cotizacion.importe:,.2f}" in contenido


class TestDesdeLaCopiaPublicada:
    def test_sirve_el_boleto_sin_folio_local(self, client, servicio_configurado, tarifa):
        """
        Es el despliegue de solo lectura: no hay folios en esta base.

        Se construye una copia publicada con un identificador que no
        corresponde a ningún folio, que es exactamente la situación del
        servicio remoto.
        """
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        huerfano = modulo_uuid.uuid4()
        BoletoPublicado.objects.create(
            uuid_folio=huerfano,
            placa_enmascarada="XYZ••••",
            estado="ABIERTO",
            entrada_en=timezone.now() - timedelta(hours=1),
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=15,
            fraccion_minutos=1,
            cobro_minimo=Decimal("0.00"),
        )
        assert not Folio.objects.filter(uuid=huerfano).exists()

        respuesta = client.get(ruta_de(huerfano), secure=True)
        assert respuesta.status_code == 200
        assert "XYZ••••" in respuesta.content.decode()


class TestAdaptador:
    def test_los_dos_origenes_producen_la_misma_vista(self, folio_abierto, publicar):
        """
        Un mismo folio debe verse igual por los dos caminos.

        Si divergieran, el cliente vería datos distintos según cómo esté
        desplegado el sistema, y el boleto dejaría de ser una sola cosa.
        """
        publicado = publicar(folio_abierto)

        por_folio = desde_folio(folio_abierto)
        por_copia = desde_publicado(publicado)

        for campo in (
            "uuid_folio",
            "placa_enmascarada",
            "estado",
            "ubicacion",
            "cuota_hora",
            "minutos_cortesia",
            "fraccion_minutos",
            "cobro_minimo",
            "tope_diario",
        ):
            assert getattr(por_folio, campo) == getattr(por_copia, campo), campo

    def test_la_vista_no_expone_datos_internos(self, folio_abierto):
        """
        El objeto que recibe la plantilla no contiene lo que no debe mostrar.

        Es protección estructural: la plantilla no puede filtrar un operador o
        un importe de caja al que no tiene acceso, aunque alguien lo intente.
        """
        vista = desde_folio(folio_abierto)

        for prohibido in (
            "placa",
            "operador_entrada",
            "operador_salida",
            "turno",
            "pago_recibido",
            "cambio",
            "observaciones",
        ):
            assert not hasattr(vista, prohibido), prohibido

    def test_la_vista_es_inmutable(self, folio_abierto):
        """Una vista modificable invitaría a «completarla» con datos vedados."""
        import dataclasses

        vista = desde_folio(folio_abierto)
        with pytest.raises(dataclasses.FrozenInstanceError):
            vista.placa_enmascarada = "ABC1234"

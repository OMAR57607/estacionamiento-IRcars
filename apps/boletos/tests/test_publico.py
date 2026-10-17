"""
Pruebas de la página que abre el cliente al leer el código QR.

Es la única superficie del sistema accesible sin credenciales, de modo que se
examinan tres cosas distintas: que muestre la cifra correcta, que no revele
nada más, y que no pueda usarse para escribir ni para sondear.
"""
import uuid as modulo_uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.boletos.dominio import RELLENO
from apps.estacionamiento import services
from apps.estacionamiento.models import Folio

pytestmark = pytest.mark.django_db


def ruta_de(uuid_folio) -> str:
    return reverse("boletos:publico", kwargs={"uuid_folio": uuid_folio})


@pytest.fixture
def boleto(folio_abierto):
    """
    El boleto **es** el folio: la página se construye leyéndolo.

    Antes había una copia publicada en medio y este accesorio la creaba. Al
    retirarse la publicación, la vista pública lee el folio directamente, de
    modo que la prueba usa lo mismo que usa producción.
    """
    return folio_abierto


class TestConsulta:
    def test_muestra_el_boleto(self, client, boleto, servicio_configurado):
        respuesta = client.get(ruta_de(boleto.uuid), secure=True)
        assert respuesta.status_code == 200

        contenido = respuesta.content.decode()
        assert f"ABC{RELLENO * 4}" in contenido
        assert "Estacionamiento de Pruebas" in contenido

    def test_el_importe_estimado_coincide_con_el_que_cobrara_la_caja(
        self, client, boleto, folio_abierto, servicio_configurado
    ):
        """
        La cifra del teléfono y la de la caseta deben ser la misma.

        Es el requisito funcional central: si el cliente ve $30.00 en su
        teléfono y el operador le cobra $35.00, la función no solo es inútil,
        es una fuente de conflicto en la caseta. Ambas cifras salen del mismo
        motor puro, y esta prueba fija esa equivalencia.
        """
        cotizacion_de_la_caja = services.cotizar_folio(folio_abierto)

        contenido = client.get(ruta_de(boleto.uuid), secure=True).content.decode()
        assert f"${cotizacion_de_la_caja.importe:,.2f}" in contenido

    def test_un_boleto_liquidado_muestra_lo_cobrado_y_no_una_estimacion(
        self, client, folio_abierto, operador, servicio_configurado
    ):
        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        boleto = folio

        contenido = client.get(ruta_de(boleto.uuid), secure=True).content.decode()
        assert "Importe cobrado" in contenido
        assert f"${folio.importe:,.2f}" in contenido
        assert "estimado" not in contenido.lower()

    def test_un_boleto_cancelado_no_muestra_importe(
        self, client, folio_abierto, operador, servicio_configurado
    ):
        services.cancelar_folio(
            uuid_folio=folio_abierto.uuid,
            motivo="placa mal capturada en la prueba",
            operador=operador,
        )

        contenido = client.get(ruta_de(folio_abierto.uuid), secure=True).content.decode()
        assert "Cancelado" in contenido
        assert "Importe estimado" not in contenido

    def test_muestra_el_precio_exhibido_al_ingresar(
        self, client, boleto, servicio_configurado
    ):
        """
        El cuadro tarifario debe estar a la vista en el propio boleto.

        Es lo que permite al cliente verificar el cobro por sí mismo, y lo que
        sostiene la leyenda de que el precio aplicable es el exhibido al
        ingresar.
        """
        contenido = client.get(ruta_de(boleto.uuid), secure=True).content.decode()
        assert "$30.00" in contenido
        assert "15 min" in contenido


    def test_exhibe_la_tolerancia_por_hora_cuando_la_tarifa_la_tiene(
        self, client, tarifa, cajones, operador, servicio_configurado
    ):
        """
        La tolerancia explica un salto de precio, así que se exhibe.

        Sin ella a la vista, el cliente que ve $60 tras una hora y cuarto no
        tiene cómo verificar el cobro, y la leyenda de precio exhibido pierde
        su respaldo: el cuadro tarifario mostraría una cuota por hora que no
        basta para reconstruir el importe.
        """
        tarifa.minutos_cortesia = 0
        tarifa.minutos_tolerancia = 15
        tarifa.save()

        folio = services.registrar_entrada(placa="ABC1234", operador=operador)
        folio.entrada_en = timezone.now() - timedelta(hours=1, minutes=16)
        folio.save(update_fields=["entrada_en"])

        contenido = client.get(ruta_de(folio.uuid), secure=True).content.decode()

        assert "Tolerancia" in contenido
        assert "15 min por hora" in contenido
        # 1:16 con cuota de $30 son dos horas completas.
        assert "$60.00" in contenido

    def test_la_tolerancia_llega_a_la_vista_del_cliente(
        self, tarifa, cajones, operador
    ):
        """
        El teléfono del cliente y la caseta cotizan con los mismos parámetros.

        La prueba vigilaba que la tolerancia sobreviviera al viaje a la nube.
        Ya no hay viaje —la página lee el folio—, pero el riesgo no
        desapareció: sigue habiendo una traducción, de folio a vista, y un
        parámetro que se quedara por el camino haría que el cliente leyera un
        importe menor al que se le cobra.
        """
        from apps.boletos.presentacion import desde_folio

        tarifa.minutos_tolerancia = 15
        tarifa.save()

        folio = services.registrar_entrada(placa="ABC1234", operador=operador)
        vista = desde_folio(folio)

        assert vista.minutos_tolerancia == 15
        assert vista.a_parametros().minutos_tolerancia == 15


class TestNoFiltraDatos:
    def test_nunca_aparece_la_placa_completa(self, client, boleto, servicio_configurado):
        contenido = client.get(ruta_de(boleto.uuid), secure=True).content.decode()
        assert "ABC1234" not in contenido

    def test_no_aparecen_los_datos_de_la_caja(
        self, client, folio_abierto, operador, servicio_configurado
    ):
        """
        La página pública no revela al operador, el turno ni el efectivo.

        Se comprueban los **valores** concretos del folio y no palabras clave:
        buscar la palabra «corte» daría un falso positivo con «cortesía», y
        una prueba que falla por su propio texto acaba desactivada. Lo que
        importa es que no aparezcan el nombre del cajero, el identificador del
        turno ni las cifras del manejo de efectivo.
        """
        from apps.estacionamiento import services_caja

        turno = services_caja.abrir_turno(
            operador=operador, fondo_inicial=Decimal("500.00")
        )
        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("200.00"),
            operador=operador,
            turno=turno,
        )
        boleto = folio

        contenido = client.get(ruta_de(boleto.uuid), secure=True).content.decode()

        assert operador.username not in contenido
        assert f"${folio.pago_recibido:,.2f}" not in contenido
        assert f"${folio.cambio:,.2f}" not in contenido
        assert f"${turno.fondo_inicial:,.2f}" not in contenido

    def test_la_vista_del_boleto_no_tiene_donde_llevar_datos_de_caja(self):
        """
        La protección es estructural, no solo de plantilla.

        `BoletoVista` es lo único que la página recibe, y carece de campos
        para la placa completa, el operador, el turno o el efectivo: una
        plantilla futura no puede exponerlos aunque su autor quiera, porque
        el dato no llega hasta ahí.

        Antes esta garantía la daba un modelo aparte, en otra base. Al vivir
        todo en la misma, el dataclass es la frontera: por eso la prueba se
        mudó aquí en lugar de retirarse.
        """
        from dataclasses import fields

        from apps.boletos.presentacion import BoletoVista

        campos = {campo.name for campo in fields(BoletoVista)}
        for prohibido in (
            "placa",
            "operador_entrada",
            "operador_salida",
            "turno",
            "pago_recibido",
            "cambio",
            "observaciones",
        ):
            assert prohibido not in campos

    def test_muestra_solo_un_fragmento_del_folio(
        self, client, boleto, servicio_configurado
    ):
        """
        El identificador completo no se imprime en la página.

        Mostrarlo facilitaría que alguien lo copiara de una captura de
        pantalla; ocho caracteres bastan para que el operador localice el
        folio en la caja.
        """
        contenido = client.get(ruta_de(boleto.uuid), secure=True).content.decode()
        assert str(boleto.uuid) not in contenido
        assert str(boleto.uuid)[:8] in contenido


class TestCabeceras:
    @pytest.fixture
    def respuesta(self, client, boleto, servicio_configurado):
        return client.get(ruta_de(boleto.uuid), secure=True)

    def test_prohibe_el_almacenamiento_en_cache(self, respuesta):
        """
        El importe cambia cada minuto: una copia en caché sería una cifra falsa.

        Y en un teléfono compartido dejaría el boleto visible para el
        siguiente que abriera el navegador.
        """
        assert "no-store" in respuesta["Cache-Control"]

    def test_prohibe_la_indexacion(self, respuesta):
        assert "noindex" in respuesta["X-Robots-Tag"]

    def test_no_filtra_la_direccion_como_referente(self, respuesta):
        """
        La dirección **es** la credencial del boleto.

        Sin esta cabecera, cualquier salto desde la página la enviaría en la
        cabecera `Referer` a un tercero.
        """
        assert respuesta["Referrer-Policy"] == "no-referrer"


class TestSuperficieDeAtaque:
    def test_la_pagina_publica_no_acepta_escrituras(
        self, client, boleto, servicio_configurado
    ):
        """
        Propiedad central del diseño: ninguna ruta pública escribe.

        Aunque la vista tuviera un fallo, no hay nada que pueda alterarse
        desde ella.

        Se admiten dos códigos porque la petición se rechaza en dos capas
        distintas, y conviene que la prueba no fije una por accidente: en un
        navegador real la protección CSRF la detiene antes de llegar a la
        vista (403), mientras que el cliente de pruebas está exento de CSRF y
        llega hasta `require_safe`, que responde 405. Lo que se verifica es
        que ninguna de las dos la deje pasar.
        """
        for metodo in ("post", "put", "patch", "delete"):
            respuesta = getattr(client, metodo)(
                ruta_de(boleto.uuid), secure=True
            )
            assert respuesta.status_code in (403, 405), (
                f"{metodo} no debería admitirse (recibido {respuesta.status_code})"
            )

    def test_un_identificador_inexistente_responde_404(
        self, client, servicio_configurado
    ):
        respuesta = client.get(ruta_de(modulo_uuid.uuid4()), secure=True)
        assert respuesta.status_code == 404

    def test_un_boleto_vencido_es_indistinguible_de_uno_inexistente(
        self, client, folio_abierto, operador, servicio_configurado
    ):
        """
        Vencido e inexistente deben contestar igual.

        Si el servicio respondiera distinto, confirmaría a quien sondea
        direcciones cuáles llegaron a existir.
        """
        folio, _ = services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        # Se retrasa la salida sin pasar por `save` del servicio: lo que se
        # examina es la caducidad de la página, no el cobro.
        Folio.objects.filter(pk=folio.pk).update(
            salida_en=timezone.now()
            - timedelta(hours=servicio_configurado.IRCARS_HORAS_VIGENCIA_BOLETO + 1)
        )

        vencido = client.get(ruta_de(folio.uuid), secure=True)
        inexistente = client.get(ruta_de(modulo_uuid.uuid4()), secure=True)

        assert vencido.status_code == inexistente.status_code == 404
        assert vencido.content == inexistente.content

    def test_un_boleto_abierto_no_vence(self, client, boleto, servicio_configurado):
        """
        Mientras el vehículo está dentro, el enlace no caduca.

        Caducar un boleto abierto por antigüedad dejaría sin consulta al
        cliente que dejó el auto un fin de semana completo.
        """
        Folio.objects.filter(uuid=boleto.uuid).update(
            entrada_en=timezone.now() - timedelta(days=30)
        )
        assert client.get(ruta_de(boleto.uuid), secure=True).status_code == 200

    def test_el_sondeo_sostenido_se_corta(self, client, boleto, servicio_configurado):
        """
        Pasado el límite por minuto y por IP, el servicio responde 429.

        El límite es holgado —un cliente puede refrescar su boleto cuanto
        quiera— y suficiente para cortar un recorrido automatizado de
        direcciones.
        """
        ruta = ruta_de(boleto.uuid)
        codigos = {client.get(ruta, secure=True).status_code for _ in range(61)}
        assert 429 in codigos

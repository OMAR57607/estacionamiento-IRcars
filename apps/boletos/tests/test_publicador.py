"""
Pruebas de la bandeja de salida y del envío.

El criterio que se verifica aquí, por encima de cualquier otro: **la caja cobra
con o sin internet.** Un fallo de publicación no debe impedir una entrada, no
debe revertir un cobro y no debe perderse en silencio.
"""
import urllib.error
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone

from apps.boletos import publicador
from apps.boletos.models import PublicacionPendiente
from apps.estacionamiento import services

pytestmark = pytest.mark.django_db


@pytest.fixture
def sin_red(monkeypatch):
    """Simula una red caída en el momento del envío."""

    def _falla(*_args, **_kwargs):
        raise urllib.error.URLError("red no disponible")

    monkeypatch.setattr("urllib.request.urlopen", _falla)


@pytest.fixture
def red_ok(monkeypatch):
    """Simula un servicio que acepta todo. Registra las cargas entregadas."""
    entregadas = []

    class _Respuesta:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def _acepta(peticion, timeout=None):
        entregadas.append(peticion)
        return _Respuesta()

    monkeypatch.setattr("urllib.request.urlopen", _acepta)
    return entregadas


class TestEncolado:
    def test_una_entrada_deja_su_publicacion_pendiente(
        self, publicacion_encendida, tarifa, cajones, operador
    ):
        services.registrar_entrada(placa="ABC1234", operador=operador)
        assert PublicacionPendiente.objects.count() == 1

    def test_sin_publicacion_configurada_no_se_encola_nada(
        self, servicio_configurado, tarifa, cajones, operador
    ):
        """
        Una instalación que no usa el servicio no debe acumular una bandeja.

        Es también la garantía de que actualizar el ejecutable no hace que una
        caseta empiece a emitir peticiones a internet sin que nadie lo decida.
        """
        servicio_configurado.IRCARS_PUBLICAR_BOLETOS = False
        services.registrar_entrada(placa="ABC1234", operador=operador)
        assert not PublicacionPendiente.objects.exists()

    def test_varios_cambios_del_mismo_folio_dejan_una_sola_fila(
        self, publicacion_encendida, folio_abierto, operador
    ):
        """
        La bandeja es una cola de trabajo, no un historial.

        Si cada cambio añadiera una fila, un corte largo de internet dejaría
        tantas publicaciones como operaciones, y se publicarían estados ya
        superados. Con el *upsert*, se envía una vez el estado final.
        """
        services.registrar_salida(
            uuid_folio=folio_abierto.uuid,
            pago_recibido=Decimal("500.00"),
            operador=operador,
        )
        assert PublicacionPendiente.objects.count() == 1

        pendiente = PublicacionPendiente.objects.get()
        assert pendiente.cuerpo["estado"] == "CERRADO"

    def test_la_bandeja_se_revierte_con_la_transaccion_del_folio(
        self, publicacion_encendida, tarifa, cajones, operador
    ):
        """
        Patrón de bandeja de salida transaccional: o quedan ambos, o ninguno.

        Sin esta propiedad podría quedar el aviso de publicar un folio que
        nunca se confirmó, y el cliente recibiría un boleto de una entrada
        inexistente.
        """
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                services.registrar_entrada(placa="ZZZ9999", operador=operador)
                assert PublicacionPendiente.objects.count() == 1
                raise RuntimeError("se revierte todo")

        assert not PublicacionPendiente.objects.exists()

    def test_un_error_de_base_de_datos_al_encolar_no_revierte_la_entrada(
        self, publicacion_encendida, tarifa, cajones, operador, monkeypatch
    ):
        """
        El encolado se hace en su propio punto de guardado.

        Sin él, capturar un error de base de datos dentro de la transacción
        del folio dejaría esa transacción inservible, y la entrada fallaría al
        confirmarse **aunque la excepción estuviera capturada**. El síntoma
        sería el peor posible: una entrada que la pantalla da por registrada y
        que no quedó en la base.

        Alcance real de esta prueba, para que nadie la lea como más de lo que
        es: **en SQLite pasa con punto de guardado y sin él**, porque SQLite
        tolera seguir operando tras un error dentro de una transacción. Se
        comprobó desactivando el punto de guardado. Lo que la prueba fija es
        el contrato visible —la entrada queda registrada—, no el mecanismo. El
        punto de guardado es lo que hace que ese contrato siga cumpliéndose en
        PostgreSQL, donde el servidor aborta la transacción completa y la
        siguiente consulta falla con `InFailedSqlTransaction`. Importa porque
        la interfaz web de caja corre sobre PostgreSQL.
        """
        from django.db import DatabaseError

        from apps.boletos.models import PublicacionPendiente as Bandeja

        original = Bandeja.objects.update_or_create

        def _falla(*args, **kwargs):
            raise DatabaseError("fallo simulado al escribir la bandeja")

        monkeypatch.setattr(Bandeja.objects, "update_or_create", _falla)
        folio = services.registrar_entrada(placa="QRS4567", operador=operador)
        monkeypatch.setattr(Bandeja.objects, "update_or_create", original)

        # La entrada quedó registrada y es consultable: la transacción se
        # confirmó pese al fallo del encolado.
        folio.refresh_from_db()
        assert folio.placa == "QRS4567"
        assert not PublicacionPendiente.objects.exists()

    def test_un_fallo_al_encolar_no_impide_registrar_la_entrada(
        self, publicacion_encendida, tarifa, cajones, operador, monkeypatch
    ):
        """
        La publicación es una comodidad; cobrar es el trabajo.

        Si la instantánea fallara —un campo nuevo mal resuelto, por ejemplo—,
        la entrada debe quedar registrada igual. Lo contrario sería que un
        detalle del boleto en la nube detuviera la caseta.
        """
        monkeypatch.setattr(
            "apps.boletos.publicador.instantanea_publica",
            lambda _folio: (_ for _ in ()).throw(ValueError("instantánea roto")),
        )

        folio = services.registrar_entrada(placa="ABC1234", operador=operador)

        assert folio.pk is not None
        assert not PublicacionPendiente.objects.exists()


class TestEntrega:
    def test_una_entrega_exitosa_vacia_la_bandeja(
        self, publicacion_encendida, folio_abierto, red_ok
    ):
        enviadas, fallidas = publicador.drenar()
        assert (enviadas, fallidas) == (1, 0)
        assert not PublicacionPendiente.objects.exists()

    def test_entrega_el_token_en_la_cabecera_y_nunca_en_la_direccion(
        self, publicacion_encendida, folio_abierto, red_ok
    ):
        """
        El token va en una cabecera, no en la URL.

        En la dirección quedaría registrado en los accesos del servidor, en el
        historial de cualquier intermediario y en los informes de error.
        """
        publicador.drenar()
        peticion = red_ok[0]

        assert peticion.get_header("Authorization", "").startswith("Bearer ")
        assert "token" not in peticion.full_url.lower()

    def test_un_fallo_de_red_conserva_la_publicacion_y_la_reprograma(
        self, publicacion_encendida, folio_abierto, sin_red
    ):
        enviadas, fallidas = publicador.drenar()
        assert (enviadas, fallidas) == (0, 1)

        pendiente = PublicacionPendiente.objects.get()
        assert pendiente.intentos == 1
        assert pendiente.proximo_intento_en > timezone.now()
        assert "contactar" in pendiente.ultimo_error.lower()

    def test_la_espera_entre_reintentos_crece(
        self, publicacion_encendida, folio_abierto, sin_red
    ):
        """
        Espera exponencial: no se martillea un servicio caído.

        Cada reintento inmediato consumiría batería y datos sin ninguna
        probabilidad adicional de éxito.
        """
        esperas = []
        for _ in range(3):
            PublicacionPendiente.objects.update(proximo_intento_en=timezone.now())
            publicador.drenar()
            pendiente = PublicacionPendiente.objects.get()
            esperas.append(pendiente.proximo_intento_en - timezone.now())

        assert esperas[0] < esperas[1] < esperas[2]

    def test_no_se_reintenta_antes_de_la_hora_programada(
        self, publicacion_encendida, folio_abierto, sin_red
    ):
        publicador.drenar()
        enviadas, fallidas = publicador.drenar()
        assert (enviadas, fallidas) == (0, 0), (
            "La segunda pasada no debe tocar una publicación que aún espera."
        )

    def test_el_token_no_aparece_en_el_error_registrado(
        self, publicacion_encendida, folio_abierto, monkeypatch
    ):
        """
        Un diagnóstico no debe filtrar la credencial.

        Es el punto por donde un secreto se escapa más a menudo: el mensaje de
        error que alguien pega en un reporte de incidencia.
        """

        def _rechaza(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                publicador.url_ingesta(), 401, "No autorizado", {}, None
            )

        monkeypatch.setattr("urllib.request.urlopen", _rechaza)
        publicador.drenar()

        pendiente = PublicacionPendiente.objects.get()
        assert publicacion_encendida.IRCARS_TOKEN_PUBLICACION not in pendiente.ultimo_error

    def test_un_rechazo_del_servicio_espera_mas_que_un_fallo_de_red(
        self, publicacion_encendida, folio_abierto, monkeypatch
    ):
        """
        Un 401 o un 400 no se arreglan reintentando: los arregla una persona.

        Distinguirlo evita que una caja mal configurada mantenga un ciclo de
        peticiones cada quince segundos durante días.
        """

        def _rechaza(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                publicador.url_ingesta(), 400, "Petición incorrecta", {}, None
            )

        monkeypatch.setattr("urllib.request.urlopen", _rechaza)
        publicador.drenar()

        pendiente = PublicacionPendiente.objects.get()
        espera = (pendiente.proximo_intento_en - timezone.now()).total_seconds()
        assert espera > publicador.ESPERA_BASE_SEGUNDOS * 4

    @pytest.mark.parametrize("codigo", [408, 429])
    def test_los_codigos_transitorios_se_tratan_como_tales(
        self, publicacion_encendida, folio_abierto, monkeypatch, codigo
    ):
        """408 y 429 sí se resuelven reintentando, y se reintentan pronto."""

        def _rechaza(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                publicador.url_ingesta(), codigo, "Transitorio", {}, None
            )

        monkeypatch.setattr("urllib.request.urlopen", _rechaza)
        publicador.drenar()

        pendiente = PublicacionPendiente.objects.get()
        espera = (pendiente.proximo_intento_en - timezone.now()).total_seconds()
        assert espera <= publicador.ESPERA_BASE_SEGUNDOS + 1

    def test_una_carga_defectuosa_no_detiene_al_resto_del_lote(
        self, publicacion_encendida, tarifa, cajones, operador, monkeypatch
    ):
        """
        Un folio problemático no debe impedir que se publique el resto del día.

        Procesar el lote fila por fila, y no en bloque, es lo que lo garantiza.
        """
        services.registrar_entrada(placa="AAA1111", operador=operador)
        services.registrar_entrada(placa="BBB2222", operador=operador)
        assert PublicacionPendiente.objects.count() == 2

        llamadas = {"n": 0}

        class _Respuesta:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def _primera_falla(*_args, **_kwargs):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise urllib.error.URLError("falla solo la primera")
            return _Respuesta()

        monkeypatch.setattr("urllib.request.urlopen", _primera_falla)
        enviadas, fallidas = publicador.drenar()

        assert (enviadas, fallidas) == (1, 1)
        assert PublicacionPendiente.objects.count() == 1


class TestTransporte:
    def test_se_rechaza_publicar_sin_cifrado(
        self, publicacion_encendida, folio_abierto, monkeypatch
    ):
        """
        El token no viaja en claro.

        Publicar por HTTP expondría la credencial a cualquiera en la ruta y
        con ella la capacidad de inyectar boletos falsos con el nombre del
        estacionamiento.
        """
        publicacion_encendida.IRCARS_URL_BOLETOS = "http://boletos.ejemplo.mx"
        publicacion_encendida.IRCARS_PERMITIR_HTTP_BOLETOS = False

        enviadas, fallidas = publicador.drenar()

        assert (enviadas, fallidas) == (0, 1)
        pendiente = PublicacionPendiente.objects.get()
        assert "https" in pendiente.ultimo_error.lower()

        # Se clasifica como permanente: una dirección mal configurada no se
        # arregla reintentando. Si se tratara como fallo de red, la caja
        # emitiría una petición cada quince segundos durante días.
        espera = (pendiente.proximo_intento_en - timezone.now()).total_seconds()
        assert espera > publicador.ESPERA_BASE_SEGUNDOS * 4

    def test_se_admite_http_solo_con_autorizacion_explicita(
        self, publicacion_encendida, folio_abierto, red_ok
    ):
        """La excepción existe para probar contra un servicio en localhost."""
        publicacion_encendida.IRCARS_URL_BOLETOS = "http://localhost:8000"
        publicacion_encendida.IRCARS_PERMITIR_HTTP_BOLETOS = True

        enviadas, _ = publicador.drenar()
        assert enviadas == 1


class TestDepuracion:
    def test_se_descarta_lo_que_ya_no_sirve_publicar(
        self, publicacion_encendida, folio_abierto
    ):
        """
        Una bandeja que crece sin límite acabaría llenando el disco de la caja.

        Pasados varios días el vehículo ya salió y el boleto no le sirve a
        nadie: se descarta y queda constancia en la bitácora.
        """
        antiguedad = timezone.now() - timedelta(
            days=publicador.DIAS_PARA_DESCARTAR + 1
        )
        PublicacionPendiente.objects.update(creada_en=antiguedad)

        assert publicador.descartar_vencidas() == 1
        assert not PublicacionPendiente.objects.exists()

    def test_no_se_descarta_lo_reciente(self, publicacion_encendida, folio_abierto):
        assert publicador.descartar_vencidas() == 0
        assert PublicacionPendiente.objects.count() == 1

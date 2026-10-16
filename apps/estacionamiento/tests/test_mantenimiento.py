"""
Pruebas del borrado de movimientos.

Es la operación más destructiva de la aplicación, así que lo que se verifica
aquí no es solo que borre, sino sobre todo **qué no debe llevarse por
delante**: los operadores, que son la identidad con la que se firma cada
movimiento, y el precio vigente, sin el cual la caseta no puede ni abrir.

Requieren base de datos: lo que se comprueba son las claves foráneas `PROTECT`
que imponen el orden del borrado, y esas viven en el esquema.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.estacionamiento import services, services_caja, services_mantenimiento
from apps.estacionamiento.domain.exceptions import ErrorConfiguracion
from apps.estacionamiento.models import Folio, Tarifa, Turno

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifa():
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=15,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def operador():
    return get_user_model().objects.create_user(
        username="duena", password="clave-de-prueba-1234", is_staff=True
    )


@pytest.fixture
def con_movimientos(tarifa, operador):
    """Un turno abierto, un cobro cerrado y un vehículo todavía dentro."""
    turno = services_caja.abrir_turno(
        operador=operador, fondo_inicial=Decimal("500.00")
    )
    entrada = timezone.now() - timedelta(hours=2)
    folio = services.registrar_entrada(
        placa="ABC-123", operador=operador, momento=entrada
    )
    services.registrar_salida(
        uuid_folio=folio.uuid, pago_recibido=Decimal("100.00"), operador=operador
    )
    services.registrar_entrada(placa="XYZ-987", operador=operador)
    return turno


class TestResumen:
    def test_cuenta_lo_que_hay_antes_de_borrar(self, con_movimientos):
        # El diálogo enseña estas cifras: si mienten, el usuario aprueba a
        # ciegas un borrado mayor del que cree.
        resumen = services_mantenimiento.resumen_movimientos()
        assert resumen["folios"] == 2
        assert resumen["folios_abiertos"] == 1
        assert resumen["turnos"] == 1
        assert resumen["turnos_abiertos"] == 1


class TestPurga:
    def test_borra_boletos_y_turnos(self, con_movimientos, operador):
        resultado = services_mantenimiento.purgar_movimientos(
            operador=operador, respaldar=False
        )
        assert Folio.objects.count() == 0
        assert Turno.objects.count() == 0
        assert resultado["folios_borrados"] == 2
        assert resultado["turnos_borrados"] == 1

    def test_conserva_los_operadores(self, con_movimientos, operador):
        # Perder al operador obliga a reinstalar: es la única pieza cuya
        # pérdida no tiene vuelta atrás desde la propia aplicación.
        services_mantenimiento.purgar_movimientos(operador=operador, respaldar=False)
        assert get_user_model().objects.filter(pk=operador.pk).exists()

    def test_conserva_el_precio_vigente(self, con_movimientos, operador, tarifa):
        services_mantenimiento.purgar_movimientos(operador=operador, respaldar=False)
        vigente = services.obtener_tarifa_vigente()
        assert vigente.pk == tarifa.pk
        assert vigente.cuota_hora == Decimal("30.00")

    def test_por_omision_no_toca_el_historico_de_precios(
        self, con_movimientos, operador, tarifa
    ):
        Tarifa.objects.create(
            nombre="Viejo", cuota_hora=Decimal("20.00"), activa=False
        )
        services_mantenimiento.purgar_movimientos(operador=operador, respaldar=False)
        assert Tarifa.objects.count() == 2

    def test_puede_limpiar_el_historico_sin_tocar_el_vigente(
        self, con_movimientos, operador, tarifa
    ):
        Tarifa.objects.create(
            nombre="Viejo", cuota_hora=Decimal("20.00"), activa=False
        )
        Tarifa.objects.create(
            nombre="Más viejo", cuota_hora=Decimal("10.00"), activa=False
        )
        resultado = services_mantenimiento.purgar_movimientos(
            operador=operador, incluir_historico_precios=True, respaldar=False
        )
        assert resultado["precios_borrados"] == 2
        assert list(Tarifa.objects.values_list("pk", flat=True)) == [tarifa.pk]
        # Y la caseta sigue pudiendo operar.
        assert services.obtener_tarifa_vigente().pk == tarifa.pk

    def test_deja_la_caseta_lista_para_operar_de_nuevo(
        self, con_movimientos, operador
    ):
        # La prueba que de verdad importa: después de borrar, el circuito
        # completo —abrir turno, entrada, cobro— vuelve a funcionar.
        services_mantenimiento.purgar_movimientos(operador=operador, respaldar=False)

        services_caja.abrir_turno(operador=operador, fondo_inicial=Decimal("200.00"))
        folio = services.registrar_entrada(placa="NUEVA-1", operador=operador)
        folio, cotizacion = services.registrar_salida(
            uuid_folio=folio.uuid, operador=operador, exigir_turno=True
        )
        assert folio.importe is not None
        assert cotizacion.es_cortesia is True

    def test_una_placa_borrada_puede_volver_a_entrar(
        self, con_movimientos, operador
    ):
        # La restricción única de «placa con folio abierto» se levanta al
        # borrar: si no, el vehículo que estaba dentro durante las pruebas no
        # podría volver a ingresar nunca.
        services_mantenimiento.purgar_movimientos(operador=operador, respaldar=False)
        assert services.registrar_entrada(placa="XYZ-987", operador=operador)


class TestControl:
    def test_el_respaldo_se_niega_dentro_de_una_transaccion(self, operador):
        # Protege contra el cuelgue: si alguien llamara al respaldo desde
        # dentro de un `atomic`, debe fallar con un motivo legible y no
        # quedarse reintentando en silencio para siempre.
        from django.db import transaction as tx

        with pytest.raises(ErrorConfiguracion):
            with tx.atomic():
                services_mantenimiento.respaldar_base()

    def test_exige_un_operador_identificado(self, con_movimientos):
        # Queda registrado a nombre de quien lo ejecuta; sin nombre, el
        # registro no sirve de nada.
        with pytest.raises(ErrorConfiguracion):
            services_mantenimiento.purgar_movimientos(
                operador=None, respaldar=False
            )
        assert Folio.objects.count() == 2

    # Sin transacción envolvente: el respaldo de SQLite no puede ejecutarse
    # dentro de una, y pytest-django envuelve cada prueba en `atomic` por
    # omisión. Es la misma condición que se da en la aplicación real, donde el
    # respaldo ocurre antes de abrir ninguna.
    @pytest.mark.django_db(transaction=True)
    def test_el_respaldo_se_escribe_antes_de_borrar(
        self, con_movimientos, operador, tmp_path
    ):
        # El respaldo es lo que convierte esto en una acción reversible. Debe
        # contener los datos de ANTES, no la base ya vacía.
        import sqlite3

        destino = services_mantenimiento.respaldar_base(
            tmp_path / "respaldo.sqlite3"
        )
        if destino is None:
            pytest.skip("El respaldo solo aplica al motor SQLite.")

        services_mantenimiento.purgar_movimientos(operador=operador, respaldar=False)
        assert Folio.objects.count() == 0

        copia = sqlite3.connect(str(destino))
        try:
            guardados = copia.execute(
                "select count(*) from estacionamiento_folio"
            ).fetchone()[0]
        finally:
            copia.close()
        assert guardados == 2


class TestPantallaWeb:
    """
    La pantalla que expone el borrado en la caja web.

    El servicio ya estaba probado; lo que se verifica aquí son las barreras,
    que es lo propio de una pantalla: quién llega, qué ve antes de pulsar y
    qué hace falta para que borre. Un borrado que ocurra sin contraseña, o al
    que llegue un cajero, convierte la operación más destructiva del sistema
    en un clic.
    """

    @pytest.fixture
    def cajero(self):
        return get_user_model().objects.create_user(
            username="cajero", password="clave-de-prueba-1234"
        )

    def test_un_cajero_no_llega_a_la_pantalla(self, client, cajero):
        from django.urls import reverse

        client.force_login(cajero)
        respuesta = client.get(reverse("web:mantenimiento"))

        assert respuesta.status_code == 302
        assert respuesta["Location"].startswith(reverse("login")), (
            "Un cajero no debe ver la pantalla de borrado"
        )

    def test_un_cajero_no_puede_borrar_aunque_envie_el_formulario(
        self, client, cajero, con_movimientos
    ):
        """
        La barrera no puede vivir solo en el enlace que no se le muestra.
        """
        from django.urls import reverse

        client.force_login(cajero)
        client.post(
            reverse("web:mantenimiento"), {"clave": "clave-de-prueba-1234"}
        )

        assert Folio.objects.exists()

    def test_muestra_lo_que_se_va_a_borrar(self, client, operador, con_movimientos):
        from django.urls import reverse

        client.force_login(operador)
        contenido = client.get(reverse("web:mantenimiento")).content.decode()

        assert "Se va a borrar" in contenido
        assert "vehículo(s) todavía dentro" in contenido, (
            "Un folio abierto pierde su entrada: tiene que avisarse antes"
        )

    def test_sin_la_contrasena_correcta_no_borra_nada(
        self, client, operador, con_movimientos
    ):
        from django.urls import reverse

        client.force_login(operador)
        respuesta = client.post(
            reverse("web:mantenimiento"), {"clave": "no-es-la-contrasena"}
        )

        assert respuesta.status_code == 200
        assert Folio.objects.exists()
        assert Turno.objects.exists()

    def test_con_la_contrasena_borra_y_conserva_lo_que_debe(
        self, client, operador, tarifa, con_movimientos
    ):
        from django.urls import reverse

        client.force_login(operador)
        respuesta = client.post(
            reverse("web:mantenimiento"), {"clave": "clave-de-prueba-1234"}
        )

        assert respuesta.status_code == 302
        assert not Folio.objects.exists()
        assert not Turno.objects.exists()
        # Lo que no debe irse con el borrado.
        assert get_user_model().objects.filter(pk=operador.pk).exists()
        assert Tarifa.objects.filter(pk=tarifa.pk, activa=True).exists()

    def test_una_visita_nunca_borra(self, client, operador, con_movimientos):
        """
        Abrir la pantalla es consultar. Si un GET borrara, bastaría con que
        alguien compartiera el enlace.
        """
        from django.urls import reverse

        client.force_login(operador)
        client.get(reverse("web:mantenimiento"))

        assert Folio.objects.exists()

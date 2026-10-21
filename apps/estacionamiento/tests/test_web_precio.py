"""
Pruebas de la pantalla del precio en la interfaz web.

Verifican lo que distingue a esta pantalla de un formulario cualquiera: que el
precio esté **bloqueado**. Son tres garantías y cada una responde a un error
que la interfaz permitía antes:

1. Desde una caja no se llega al formulario. Cobrar de menos y quedarse la
   diferencia empieza por poder bajar el precio.
2. El formulario no existe hasta que se confirma la contraseña, ni siquiera
   oculto en el HTML, y un POST que se la salte no guarda nada.
3. El formulario llega con los valores en uso. Antes arrancaba con valores
   fijos de ejemplo, así que «subir un peso la hora» reescribía en silencio la
   cortesía, la fracción y el tope.

No se reexamina aquí el cálculo del importe —eso está en `test_tarifas.py`—,
sino el recorrido que lleva a cambiarlo.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento.models import Tarifa, TipoVehiculo

pytestmark = pytest.mark.django_db

CLAVE = "clave-de-prueba-1234"


@pytest.fixture
def vigente():
    """
    Precio en uso con los cinco parámetros distintos de los valores de arranque.

    Que ninguno coincida con las constantes del código es deliberado: es lo que
    permite demostrar que los valores iniciales del formulario salen del precio
    vigente y no de un valor fijo.
    """
    return Tarifa.objects.create(
        nombre="Precio 2026-01-01 08:00:00",
        cuota_hora=Decimal("42.00"),
        minutos_cortesia=20,
        fraccion_minutos=15,
        cobro_minimo=Decimal("12.00"),
        tope_diario=Decimal("180.00"),
        activa=True,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajero():
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def administrativo():
    return get_user_model().objects.create_user(
        username="dueno", password=CLAVE, is_staff=True
    )


def _precios_de_auto():
    """
    Los precios de automóvil, que es de lo que habla esta suite.

    El cuadro tarifario tiene una fila por tipo de vehículo y la base de
    pruebas trae el precio de motocicleta que siembra su migración, así que
    contar en global mezclaría dos cosas distintas.
    """
    return Tarifa.objects.filter(tipo_vehiculo=TipoVehiculo.AUTOMOVIL)


def _desbloquear(client, clave=CLAVE):
    return client.post(
        reverse("web:tarifa_cambiar"), {"accion": "desbloquear", "clave": clave}
    )


class TestConsulta:
    def test_el_cajero_consulta_el_precio_pero_no_puede_teclearlo(
        self, client, cajero, vigente
    ):
        client.force_login(cajero)
        respuesta = client.get(reverse("web:tarifa"))

        assert respuesta.status_code == 200
        contenido = respuesta.content.decode()
        # La cifra que el cliente pregunta, visible, y el candado a la vista.
        assert "42.00" in contenido
        assert "Precio bloqueado" in contenido
        # Ningún campo del precio que se pueda teclear por descuido.
        for campo in (
            "cuota_hora",
            "minutos_cortesia",
            "fraccion_minutos",
            "minutos_tolerancia",
            "cobro_minimo",
            "tope_diario",
        ):
            assert f'name="{campo}"' not in contenido
        # Y ningún enlace al cambio: desde una caja se consulta, no se modifica.
        assert reverse("web:tarifa_cambiar") not in contenido

    def test_la_pantalla_muestra_los_ejemplos_calculados_con_el_motor(
        self, client, cajero, vigente
    ):
        client.force_login(cajero)
        respuesta = client.get(reverse("web:tarifa"))

        auto = next(
            p
            for p in respuesta.context["precios"]
            if p["tipo"] == TipoVehiculo.AUTOMOVIL
        )
        ejemplos = {fila["etiqueta"]: fila for fila in auto["ejemplos"]}
        # 20 minutos de cortesía: a los 15 todavía no se cobra.
        assert ejemplos["15 minutos"]["es_cortesia"]
        # Una hora: 4 bloques de 15 min a 42.00/h → 42.00, por encima del
        # mínimo de 12.00 y por debajo del tope de 180.00.
        assert ejemplos["1 hora"]["importe"] == Decimal("42.00")
        # 24 horas tocarían 1,008.00 y el tope diario las acota.
        assert ejemplos["24 horas"]["importe"] == Decimal("180.00")

    def test_la_pantalla_del_precio_no_acepta_envios(self, client, administrativo):
        # Aunque se conozca la dirección anterior, ya no hay nada que enviar
        # aquí: el cambio vive en su propia dirección, detrás de la contraseña.
        client.force_login(administrativo)
        assert client.post(reverse("web:tarifa"), {}).status_code == 405


class TestSoloAdministrativo:
    def test_el_cajero_no_llega_al_formulario(self, client, cajero, vigente):
        client.force_login(cajero)
        respuesta = client.get(reverse("web:tarifa_cambiar"))

        assert respuesta.status_code == 302
        assert respuesta["Location"] == reverse("web:tarifa")

    def test_el_cajero_no_puede_desbloquear_con_su_contrasena(
        self, client, cajero, vigente
    ):
        client.force_login(cajero)
        respuesta = _desbloquear(client)

        # No basta con conocer una contraseña válida: hace falta el perfil.
        assert respuesta.status_code == 302
        assert respuesta["Location"] == reverse("web:tarifa")

    def test_el_cajero_no_puede_guardar_un_precio(self, client, cajero, vigente):
        client.force_login(cajero)
        respuesta = client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "cuota_hora": "1.00",
                "minutos_cortesia": "0",
                "fraccion_minutos": "1",
                "minutos_tolerancia": "0",
                "cobro_minimo": "0.00",
            },
        )

        assert respuesta.status_code == 302
        assert _precios_de_auto().count() == 1
        vigente.refresh_from_db()
        assert vigente.cuota_hora == Decimal("42.00")


class TestDesbloqueo:
    def test_sin_contrasena_no_hay_formulario_en_la_pagina(
        self, client, administrativo, vigente
    ):
        client.force_login(administrativo)
        respuesta = client.get(reverse("web:tarifa_cambiar"))

        assert respuesta.status_code == 200
        # El formulario no está escondido ni deshabilitado: no se envía.
        assert "form" not in respuesta.context
        assert "form_clave" in respuesta.context
        assert "cuota_hora" not in respuesta.content.decode()

    def test_una_contrasena_incorrecta_no_abre_el_formulario(
        self, client, administrativo, vigente
    ):
        client.force_login(administrativo)
        respuesta = _desbloquear(client, clave="la-que-no-es")

        assert respuesta.status_code == 200
        assert "form" not in respuesta.context
        assert "cuota_hora" not in respuesta.content.decode()

    def test_la_contrasena_correcta_abre_el_formulario(
        self, client, administrativo, vigente
    ):
        client.force_login(administrativo)
        respuesta = _desbloquear(client)

        assert respuesta.status_code == 200
        assert "form" in respuesta.context
        assert "form_clave" not in respuesta.context

    def test_un_envio_sin_desbloqueo_no_guarda_nada(
        self, client, administrativo, vigente
    ):
        """
        El candado no es de la plantilla, es de la vista.

        Quien reenvíe el formulario desde una pestaña vieja, o lo arme a mano,
        no cambia el precio: sin desbloqueo vigente en la sesión, el envío
        devuelve la pantalla de la contraseña.
        """
        client.force_login(administrativo)
        respuesta = client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "cuota_hora": "1.00",
                "minutos_cortesia": "0",
                "fraccion_minutos": "1",
                "minutos_tolerancia": "0",
                "cobro_minimo": "0.00",
            },
        )

        assert respuesta.status_code == 200
        assert "form_clave" in respuesta.context
        assert _precios_de_auto().count() == 1

    def test_el_desbloqueo_no_sobrevive_al_cambio(
        self, client, administrativo, vigente
    ):
        # Guardado el precio, vuelve a quedar bloqueado: la siguiente visita
        # pide otra vez la contraseña.
        client.force_login(administrativo)
        _desbloquear(client)
        client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "cuota_hora": "50.00",
                "minutos_cortesia": "20",
                "fraccion_minutos": "15",
                "minutos_tolerancia": "0",
                "cobro_minimo": "12.00",
                "tope_diario": "180.00",
            },
        )

        respuesta = client.get(reverse("web:tarifa_cambiar"))
        assert "form_clave" in respuesta.context

    def test_el_desbloqueo_caduca(self, client, administrativo, vigente):
        from apps.estacionamiento.web import views

        client.force_login(administrativo)
        _desbloquear(client)

        sesion = client.session
        sesion[views.CLAVE_DESBLOQUEO] = (
            timezone.now() - timedelta(minutes=views.MINUTOS_DESBLOQUEO + 1)
        ).isoformat()
        sesion.save()

        respuesta = client.get(reverse("web:tarifa_cambiar"))
        assert "form_clave" in respuesta.context

    def test_la_primera_configuracion_no_pide_contrasena(
        self, client, administrativo
    ):
        """
        Sin precio todavía no hay nada que proteger.

        El operador acaba de autenticarse para entrar y la caseta no puede
        operar: pedir la contraseña otra vez sería un obstáculo sin control que
        lo respalde.
        """
        client.force_login(administrativo)
        respuesta = client.get(reverse("web:tarifa_cambiar"))

        assert respuesta.status_code == 200
        assert "form" in respuesta.context


class TestToleranciaPorHora:
    """
    La tolerancia se captura en la misma pantalla que el resto del precio.

    No basta con que el motor la entienda: si no se puede teclear ni se ve en
    la tabla de ejemplos, el dueño no tiene forma de comprobar qué cobrará su
    caja antes de poner el precio en vigencia.
    """

    def test_se_guarda_con_el_precio(self, client, administrativo, vigente):
        client.force_login(administrativo)
        _desbloquear(client)

        respuesta = client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "cuota_hora": "30.00",
                "minutos_cortesia": "0",
                "fraccion_minutos": "1",
                "minutos_tolerancia": "15",
                "cobro_minimo": "0.00",
            },
        )

        assert respuesta.status_code == 302
        assert _precios_de_auto().get(activa=True).minutos_tolerancia == 15

    def test_los_ejemplos_muestran_el_salto_de_precio(
        self, client, administrativo, vigente
    ):
        """
        Las dos filas que rodean la tolerancia son las que la hacen visible.

        Con ejemplos solo en horas exactas, la tabla mostraría las mismas
        cifras con tolerancia y sin ella, y quien fija el precio no vería la
        consecuencia de lo que acaba de teclear.
        """
        _precios_de_auto().update(
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=0,
            fraccion_minutos=1,
            minutos_tolerancia=15,
            cobro_minimo=Decimal("0.00"),
            tope_diario=None,
        )
        client.force_login(administrativo)

        contenido = client.get(reverse("web:tarifa")).content.decode()

        assert "1 h 10 min" in contenido
        assert "$35.00" in contenido, "Dentro de la tolerancia: proporcional"
        assert "1 h 16 min" in contenido
        assert "$60.00" in contenido, "Al rebasarla: dos horas completas"

    def test_una_tolerancia_de_una_hora_se_rechaza(
        self, client, administrativo, vigente
    ):
        """
        El formulario no deja guardar lo que el motor rechazaría.
        """
        client.force_login(administrativo)
        _desbloquear(client)

        respuesta = client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "cuota_hora": "30.00",
                "minutos_cortesia": "0",
                "fraccion_minutos": "1",
                "minutos_tolerancia": "60",
                "cobro_minimo": "0.00",
            },
        )

        assert respuesta.status_code == 200
        assert _precios_de_auto().count() == 1


class TestReglaEnPalabras:
    """
    La pantalla del precio dice cómo se cobra, además de mostrarlo.

    Un precio con «bloques de 15 minutos» y otro con «15 de tolerancia»
    producen tablas de ejemplos parecidas y ninguno de los dos es inválido.
    Así se configuró mal en producción. La frase los distingue.
    """

    def test_la_pantalla_declara_la_tolerancia_vigente(self, client, cajero, vigente):
        _precios_de_auto().update(
            cuota_hora=Decimal("15.00"),
            minutos_cortesia=0,
            fraccion_minutos=1,
            minutos_tolerancia=15,
            cobro_minimo=Decimal("15.00"),
            tope_diario=None,
        )
        client.force_login(cajero)

        contenido = client.get(reverse("web:tarifa")).content.decode()

        assert "15 minutos de tolerancia" in contenido
        assert "la hora completa" in contenido

    def test_la_pantalla_avisa_cuando_no_hay_tolerancia(self, client, cajero, vigente):
        _precios_de_auto().update(
            cuota_hora=Decimal("15.00"),
            minutos_cortesia=0,
            fraccion_minutos=15,
            minutos_tolerancia=0,
            cobro_minimo=Decimal("15.00"),
            tope_diario=None,
        )
        client.force_login(cajero)

        contenido = client.get(reverse("web:tarifa")).content.decode()

        assert "No hay tolerancia por hora" in contenido
        assert "bloques de 15 minutos" in contenido


class TestValoresIniciales:
    def test_el_formulario_llega_con_los_valores_en_uso(
        self, client, administrativo, vigente
    ):
        client.force_login(administrativo)
        iniciales = _desbloquear(client).context["form"].initial

        assert iniciales == {
            "cuota_hora": Decimal("42.00"),
            "minutos_cortesia": 20,
            "fraccion_minutos": 15,
            "minutos_tolerancia": 0,
            "cobro_minimo": Decimal("12.00"),
            "tope_diario": Decimal("180.00"),
        }

    def test_el_formulario_no_pide_nombre(self, client, administrativo, vigente):
        # Quien cambia el precio es el dueño, que no tiene por qué bautizarlo.
        client.force_login(administrativo)
        assert "nombre" not in _desbloquear(client).context["form"].fields

    def test_cambiar_la_cuota_respeta_los_parametros_no_tocados(
        self, client, administrativo, vigente
    ):
        """
        El arreglo de fondo: lo que no se toca, no cambia.

        Se reenvía exactamente lo que trae el formulario, con la cuota por hora
        cambiada y nada más. Antes, los campos arrancaban con valores fijos de
        ejemplo, de modo que esta operación —la más común de todas— reescribía
        en silencio la cortesía (15), la fracción (1) y el tope (vacío).
        """
        client.force_login(administrativo)
        iniciales = _desbloquear(client).context["form"].initial

        envio = {clave: str(valor) for clave, valor in iniciales.items()}
        envio["accion"] = "guardar"
        envio["cuota_hora"] = "48.00"

        respuesta = client.post(reverse("web:tarifa_cambiar"), envio)
        assert respuesta.status_code == 302
        assert respuesta["Location"] == reverse("web:tarifa")

        nueva = _precios_de_auto().get(activa=True)
        assert nueva.cuota_hora == Decimal("48.00")
        assert nueva.minutos_cortesia == 20
        assert nueva.fraccion_minutos == 15
        assert nueva.cobro_minimo == Decimal("12.00")
        assert nueva.tope_diario == Decimal("180.00")

    def test_el_precio_anterior_se_conserva_desactivado(
        self, client, administrativo, vigente
    ):
        # Nunca se edita en sitio: así puede demostrarse qué precio estuvo
        # exhibido en cada fecha, y los folios ya cobrados mantienen el suyo.
        client.force_login(administrativo)
        iniciales = _desbloquear(client).context["form"].initial
        envio = {clave: str(valor) for clave, valor in iniciales.items()}
        envio["accion"] = "guardar"
        envio["cuota_hora"] = "48.00"
        client.post(reverse("web:tarifa_cambiar"), envio)

        vigente.refresh_from_db()
        assert not vigente.activa
        assert vigente.cuota_hora == Decimal("42.00")
        assert _precios_de_auto().count() == 2

    def test_el_nombre_se_genera_con_marca_de_tiempo(
        self, client, administrativo, vigente
    ):
        client.force_login(administrativo)
        iniciales = _desbloquear(client).context["form"].initial
        envio = {clave: str(valor) for clave, valor in iniciales.items()}
        envio["accion"] = "guardar"
        envio["cuota_hora"] = "48.00"
        client.post(reverse("web:tarifa_cambiar"), envio)

        nueva = _precios_de_auto().get(activa=True)
        assert nueva.nombre.startswith("Automóvil "), (
            "El nombre lleva el tipo por delante: con dos precios creados en "
            "el mismo segundo, es lo que los distingue"
        )
        # El modelo lo exige único: dos cambios no pueden chocar de nombre.
        assert nueva.nombre != vigente.nombre

    def test_un_precio_invalido_no_se_guarda_y_devuelve_el_motivo(
        self, client, administrativo, vigente
    ):
        client.force_login(administrativo)
        _desbloquear(client)
        respuesta = client.post(
            reverse("web:tarifa_cambiar"),
            {
                "accion": "guardar",
                "cuota_hora": "48.00",
                "minutos_cortesia": "20",
                # Fuera del rango que acepta el motor de tarifas.
                "fraccion_minutos": "90",
                "minutos_tolerancia": "0",
                "cobro_minimo": "12.00",
            },
        )

        assert respuesta.status_code == 200
        assert _precios_de_auto().count() == 1
        # El desbloqueo sigue en pie: el intento falló, no hay por qué volver a
        # teclear la contraseña para corregir un dígito.
        assert "form" in respuesta.context

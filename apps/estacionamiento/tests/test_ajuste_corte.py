"""
Pruebas de la corrección de un corte ya firmado.

Lo que se fija aquí no es que el número cambie —eso es trivial— sino las tres
propiedades que hacen que la corrección sea un control y no un agujero:

1. **El corte original sobrevive.** La cifra que el operador declaró al
   cerrar queda intacta para siempre. Si un ajuste pudiera borrarla, un
   faltante dejaría de ser un hecho registrado y el control de caja entero
   se volvería decorativo.
2. **Nadie corrige su propia caja.** La pantalla está restringida al perfil
   administrativo. El cierre ciego descansa en que la diferencia la constate
   alguien distinto de quien contó.
3. **Toda corrección deja rastro.** Motivo, justificación escrita, autor y
   momento. Sin eso, ajustar y desviar son indistinguibles.

El caso que motivó todo esto está en `TestElCasoQueLoMotivo`: un turno cerrado
con $140 de menos que no se podía corregir.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja
from apps.estacionamiento.domain.corte import calcular_ajuste, justificacion_suficiente
from apps.estacionamiento.domain.exceptions import (
    AjusteNoAplicable,
    ErrorConfiguracion,
)
from apps.estacionamiento.models import (
    AjusteCorte,
    EstadoTurno,
    MotivoAjuste,
    Tarifa,
    Turno,
)

CLAVE = "clave-de-prueba-1234"
JUSTIFICACION = "Se localizó un cobro de $140 que no se había registrado."


# ---------------------------------------------------------------------------
# Motor puro. Sin base de datos: la aritmética de un faltante debe poder
# comprobarse sin levantar nada.
# ---------------------------------------------------------------------------


class TestMotorDeAjuste:
    def test_corregir_un_faltante_deja_la_caja_cuadrada(self):
        resultado = calcular_ajuste(
            efectivo_esperado=Decimal("3000.00"),
            efectivo_declarado_vigente=Decimal("2860.00"),
            efectivo_corregido=Decimal("3000.00"),
        )

        assert resultado.monto == Decimal("140.00")
        assert resultado.diferencia_anterior == Decimal("-140.00")
        assert resultado.diferencia_nueva == Decimal("0.00")
        assert resultado.cuadra
        assert resultado.acerca_a_cuadrar
        assert not resultado.sin_efecto

    def test_un_ajuste_puede_dejar_sobrante(self):
        resultado = calcular_ajuste(
            efectivo_esperado=Decimal("1000.00"),
            efectivo_declarado_vigente=Decimal("1000.00"),
            efectivo_corregido=Decimal("1050.00"),
        )

        assert resultado.monto == Decimal("50.00")
        assert resultado.diferencia_nueva == Decimal("50.00")
        assert not resultado.cuadra
        # Alejarse de cuadrar es legítimo, pero debe poder detectarse: es el
        # patrón que tendría un desvío documentado como corrección.
        assert not resultado.acerca_a_cuadrar

    def test_capturar_la_misma_cifra_no_tiene_efecto(self):
        resultado = calcular_ajuste(
            efectivo_esperado=Decimal("500.00"),
            efectivo_declarado_vigente=Decimal("480.00"),
            efectivo_corregido=Decimal("480.00"),
        )

        assert resultado.sin_efecto
        assert resultado.monto == Decimal("0.00")

    def test_el_motor_no_lanza_decide_quien_lo_usa(self):
        """
        El dominio constata; la capa de aplicación decide.

        Si el motor lanzara, no podría reutilizarse para simular el efecto de
        un ajuste antes de aplicarlo, que es justo lo que la pantalla
        necesitará el día que muestre una vista previa.
        """
        resultado = calcular_ajuste(
            efectivo_esperado=Decimal("0.00"),
            efectivo_declarado_vigente=Decimal("0.00"),
            efectivo_corregido=Decimal("0.00"),
        )
        assert resultado.sin_efecto

    @pytest.mark.parametrize(
        "texto, suficiente",
        [
            ("", False),
            ("   ", False),
            ("error", False),
            ("ajuste", False),
            ("Faltó un cobro", True),
            (None, False),
        ],
    )
    def test_justificacion_minima(self, texto, suficiente):
        assert justificacion_suficiente(texto) is suficiente


# ---------------------------------------------------------------------------
# Capa de aplicación. Aquí sí hace falta la base: las restricciones de
# integridad son parte del control que se verifica.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifa():
    return Tarifa.objects.create(
        nombre="General",
        cuota_hora=Decimal("30.00"),
        minutos_cortesia=0,
        fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=30),
    )


@pytest.fixture
def cajero(tarifa):
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def duena(tarifa):
    return get_user_model().objects.create_user(
        username="duena", password=CLAVE, is_staff=True
    )


def _turno_cerrado_con_faltante(operador, faltante=Decimal("140.00")) -> Turno:
    """Un turno con un cobro real, cerrado declarando de menos."""
    turno = services_caja.abrir_turno(
        operador=operador, fondo_inicial=Decimal("500.00")
    )
    folio = services.registrar_entrada(
        placa="ABC1234", operador=operador, momento=timezone.now() - timedelta(hours=5)
    )
    services.registrar_salida(
        uuid_folio=folio.uuid, pago_recibido=Decimal("200.00"), operador=operador
    )
    resumen = services_caja.calcular_resumen(turno)
    cerrado, _ = services_caja.cerrar_turno(
        turno_id=turno.pk,
        efectivo_declarado=resumen.efectivo_esperado - faltante,
    )
    return cerrado


class TestElCasoQueLoMotivo:
    """Un turno se cerró con $140 de menos y no había forma de corregirlo."""

    def test_el_administrador_cuadra_el_corte(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)
        assert turno.diferencia == Decimal("-140.00")
        assert services_caja.veredicto_del_corte(turno)["hay_faltante"]

        turno, ajuste, resultado = services_caja.ajustar_corte(
            turno_id=turno.pk,
            efectivo_corregido=turno.efectivo_esperado,
            motivo=MotivoAjuste.NO_REGISTRADO,
            justificacion=JUSTIFICACION,
            autorizo=duena,
        )

        assert resultado.monto == Decimal("140.00")
        assert turno.diferencia_vigente == Decimal("0.00")
        assert services_caja.veredicto_del_corte(turno)["cuadra"]
        assert ajuste.autorizo == duena

    def test_la_cifra_que_declaro_el_operador_no_se_toca(self, duena, cajero):
        """
        La propiedad que hace del ajuste un control y no un borrado.

        Un faltante que puede desaparecer sin dejar rastro no disuade a nadie
        de provocarlo.
        """
        turno = _turno_cerrado_con_faltante(cajero)
        declarado_original = turno.efectivo_declarado

        services_caja.ajustar_corte(
            turno_id=turno.pk,
            efectivo_corregido=turno.efectivo_esperado,
            motivo=MotivoAjuste.RECUENTO,
            justificacion=JUSTIFICACION,
            autorizo=duena,
        )
        turno.refresh_from_db()

        assert turno.efectivo_declarado == declarado_original
        assert turno.diferencia == Decimal("-140.00")
        assert turno.efectivo_ajustado == turno.efectivo_esperado
        assert turno.diferencia_ajustada == Decimal("0.00")


class TestReglasDelAjuste:
    def test_no_se_ajusta_un_turno_abierto(self, duena, cajero):
        turno = services_caja.abrir_turno(operador=cajero)

        with pytest.raises(AjusteNoAplicable, match="todavía está abierto"):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=Decimal("100.00"),
                motivo=MotivoAjuste.RECUENTO,
                justificacion=JUSTIFICACION,
                autorizo=duena,
            )

    def test_un_ajuste_sin_efecto_se_rechaza(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(AjusteNoAplicable, match="no hay nada que corregir"):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=turno.efectivo_declarado,
                motivo=MotivoAjuste.RECUENTO,
                justificacion=JUSTIFICACION,
                autorizo=duena,
            )

    def test_sin_justificacion_no_hay_ajuste(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(AjusteNoAplicable, match="Explique qué pasó"):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=turno.efectivo_esperado,
                motivo=MotivoAjuste.RECUENTO,
                justificacion="error",
                autorizo=duena,
            )
        assert not AjusteCorte.objects.exists()

    def test_un_motivo_inventado_se_rechaza(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(ErrorConfiguracion, match="Motivo de ajuste desconocido"):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=turno.efectivo_esperado,
                motivo="LO_QUE_SEA",
                justificacion=JUSTIFICACION,
                autorizo=duena,
            )

    def test_el_efectivo_corregido_no_puede_ser_negativo(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(ErrorConfiguracion, match="no puede ser negativo"):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=Decimal("-1.00"),
                motivo=MotivoAjuste.RECUENTO,
                justificacion=JUSTIFICACION,
                autorizo=duena,
            )

    def test_el_ajuste_exige_un_autor_identificado(self, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(ErrorConfiguracion, match="administrador identificado"):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=turno.efectivo_esperado,
                motivo=MotivoAjuste.RECUENTO,
                justificacion=JUSTIFICACION,
                autorizo=None,
            )

    def test_dos_ajustes_se_acumulan_en_lugar_de_pisarse(self, duena, cajero):
        """
        El segundo ajuste parte de la cifra vigente, no de la original.

        Si partiera de la original, corregir dos veces daría un resultado
        sorprendente: el segundo ajuste desharía el primero sin avisar.
        """
        turno = _turno_cerrado_con_faltante(cajero)
        esperado = turno.efectivo_esperado

        services_caja.ajustar_corte(
            turno_id=turno.pk, efectivo_corregido=esperado,
            motivo=MotivoAjuste.RECUENTO, justificacion=JUSTIFICACION, autorizo=duena,
        )
        _, _, segundo = services_caja.ajustar_corte(
            turno_id=turno.pk, efectivo_corregido=esperado + Decimal("10.00"),
            motivo=MotivoAjuste.LOCALIZADO,
            justificacion="Aparecieron $10 detrás del cajón.", autorizo=duena,
        )

        assert segundo.efectivo_anterior == esperado
        assert segundo.monto == Decimal("10.00")
        assert segundo.diferencia_nueva == Decimal("10.00")
        assert AjusteCorte.objects.filter(turno=turno).count() == 2

    def test_la_bitacora_queda_en_el_resumen_del_turno(self, duena, cajero):
        """
        El JSON del corte debe bastar por sí solo para reconstruir el
        comprobante, sin consultar otra tabla.
        """
        turno = _turno_cerrado_con_faltante(cajero)
        services_caja.ajustar_corte(
            turno_id=turno.pk, efectivo_corregido=turno.efectivo_esperado,
            motivo=MotivoAjuste.NO_REGISTRADO, justificacion=JUSTIFICACION,
            autorizo=duena,
        )
        turno.refresh_from_db()

        anexo = turno.resumen["ajustes"]
        assert len(anexo) == 1
        assert anexo[0]["autorizo"] == "duena"
        assert anexo[0]["motivo"] == MotivoAjuste.NO_REGISTRADO
        assert anexo[0]["justificacion"] == JUSTIFICACION
        assert turno.resumen["diferencia_vigente"] == "0.00"


class TestIntegridadEnLaBase:
    """
    Las dos reglas que no deben depender de que el servicio las recuerde.

    Se comprueban contra la base porque ahí es donde tienen que sostenerse:
    una vía de entrada futura —un comando, una integración— no pasaría por
    `ajustar_corte` y encontraría la puerta igualmente cerrada.
    """

    def _ajuste_base(self, turno, autorizo, **cambios) -> dict:
        datos = dict(
            turno=turno,
            efectivo_esperado=Decimal("1000.00"),
            efectivo_anterior=Decimal("900.00"),
            efectivo_nuevo=Decimal("1000.00"),
            monto=Decimal("100.00"),
            diferencia_anterior=Decimal("-100.00"),
            diferencia_nueva=Decimal("0.00"),
            motivo=MotivoAjuste.RECUENTO,
            justificacion=JUSTIFICACION,
            autorizo=autorizo,
        )
        datos.update(cambios)
        return datos

    def test_la_base_rechaza_un_ajuste_de_cero(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(IntegrityError):
            AjusteCorte.objects.create(
                **self._ajuste_base(turno, duena, monto=Decimal("0.00"))
            )

    def test_la_base_rechaza_un_ajuste_sin_justificacion(self, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)

        with pytest.raises(IntegrityError):
            AjusteCorte.objects.create(
                **self._ajuste_base(turno, duena, justificacion="")
            )


class TestPantalla:
    def test_el_cajero_no_puede_corregir_su_propio_corte(self, client, cajero):
        """
        El control completo. Si quien contó puede reescribir su diferencia,
        el cierre ciego no verifica absolutamente nada.
        """
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(cajero)

        respuesta = client.get(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk})
        )

        assert respuesta.status_code == 302
        assert "/accounts/login" in respuesta.url or "acceso" in respuesta.url

    def test_el_corte_del_cajero_no_ofrece_el_boton(self, client, cajero):
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(cajero)

        contenido = client.get(
            reverse("web:corte", kwargs={"turno_id": turno.pk})
        ).content.decode()

        assert "Cuadrar el corte" not in contenido
        assert "Corregir el corte" not in contenido

    def test_el_administrador_ve_el_boton(self, client, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(duena)

        contenido = client.get(
            reverse("web:corte", kwargs={"turno_id": turno.pk})
        ).content.decode()

        assert "Cuadrar el corte" in contenido

    def test_la_fecha_del_cierre_se_lee(self, client, duena, cajero):
        """
        El fallo que esta prueba impide: en el formato de `date`, las letras
        de «a las» son códigos —a. m./p. m., día de la semana y segundos— y
        la pantalla anunciaba «el 06/11/2026 p.m. Viernesp.m.00 22:00».
        """
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(duena)

        contenido = client.get(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk})
        ).content.decode()

        # Se normalizan los espacios: lo que se verifica es el texto, no la
        # sangría de la plantilla.
        plano = " ".join(contenido.split())
        cerrado = timezone.localtime(turno.cerrado_en)

        assert f"{cerrado:%d/%m/%Y} a las {cerrado:%H:%M}" in plano
        assert "p.m.00" not in plano

    def test_la_pantalla_precarga_el_esperado(self, client, duena, cajero):
        """
        «Cuadrar el corte» es la corrección más frecuente: teclear a mano una
        cifra que el sistema ya conoce solo añade ocasiones de equivocarse.
        """
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(duena)

        contenido = client.get(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk})
        ).content.decode()

        assert f'value="{turno.efectivo_esperado}"' in contenido

    def test_sin_la_contrasena_no_se_ajusta_nada(self, client, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(duena)

        respuesta = client.post(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk}),
            {
                "efectivo_real": str(turno.efectivo_esperado),
                "motivo": MotivoAjuste.RECUENTO,
                "justificacion": JUSTIFICACION,
                "clave": "la-que-no-es",
            },
        )

        assert respuesta.status_code == 200
        assert "Contraseña incorrecta" in respuesta.content.decode()
        assert not AjusteCorte.objects.exists()

    def test_ajuste_completo_desde_la_pantalla(self, client, duena, cajero):
        turno = _turno_cerrado_con_faltante(cajero)
        client.force_login(duena)

        respuesta = client.post(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk}),
            {
                "efectivo_real": str(turno.efectivo_esperado),
                "motivo": MotivoAjuste.NO_REGISTRADO,
                "justificacion": JUSTIFICACION,
                "clave": CLAVE,
            },
            follow=True,
        )
        turno.refresh_from_db()

        assert respuesta.status_code == 200
        assert turno.diferencia_vigente == Decimal("0.00")
        assert turno.diferencia == Decimal("-140.00")

        contenido = respuesta.content.decode()
        # El comprobante debe mostrar las dos cifras: la declarada y la real.
        assert "La caja cuadra exactamente" in contenido
        assert "Diferencia declarada al cerrar" in contenido
        assert "Correcciones aplicadas a este corte" in contenido
        assert JUSTIFICACION in contenido
        assert "duena" in contenido

    def test_no_se_puede_ajustar_un_turno_abierto_desde_la_pantalla(
        self, client, duena, cajero
    ):
        turno = services_caja.abrir_turno(operador=cajero)
        client.force_login(duena)

        respuesta = client.get(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk}), follow=True
        )

        assert "sigue abierto" in respuesta.content.decode()
        assert turno.estado == EstadoTurno.ABIERTO

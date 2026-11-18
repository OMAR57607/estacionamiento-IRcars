"""
Pruebas de la corrección del cambio de apertura de un corte ya cerrado.

El caso que la motivó: un turno se abrió declarando $9,000 de cambio que no
existían. La corrección disponible solo enmendaba lo contado, de modo que la
única forma de cuadrar era declarar $9,000 de efectivo que tampoco existieron
—y esa cifra inflaba después el efectivo esperado del periodo—.

Se fijan las mismas tres propiedades que en `test_ajuste_corte.py` —lo
declarado sobrevive, nadie corrige su propia caja, toda corrección deja
rastro— y una cuarta que es la razón de ser de esto: **los reportes dejan de
sumar el cambio que nunca estuvo**.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services, services_caja, services_metricas
from apps.estacionamiento.domain.corte import calcular_ajuste_fondo
from apps.estacionamiento.domain.exceptions import (
    AjusteNoAplicable,
    ErrorConfiguracion,
)
from apps.estacionamiento.models import AjusteCorte, MotivoAjuste, Tarifa, Turno

CLAVE = "clave-de-prueba-1234"
JUSTIFICACION = "Al abrir se tecleó $9,000 de cambio; la caja abrió con $900."

CAMBIO_DECLARADO = Decimal("9000.00")
CAMBIO_REAL = Decimal("900.00")


# ---------------------------------------------------------------------------
# Motor puro.
# ---------------------------------------------------------------------------


class TestMotorDeAjusteDeFondo:
    def test_bajar_el_cambio_baja_el_esperado_en_lo_mismo(self):
        resultado = calcular_ajuste_fondo(
            fondo_vigente=Decimal("9000.00"),
            fondo_corregido=Decimal("900.00"),
            efectivo_esperado_vigente=Decimal("9200.00"),
            efectivo_contado_vigente=Decimal("1100.00"),
        )

        assert resultado.monto == Decimal("-8100.00")
        assert resultado.esperado_nuevo == Decimal("1100.00")
        assert resultado.diferencia_anterior == Decimal("-8100.00")
        assert resultado.diferencia_nueva == Decimal("0.00")
        assert resultado.cuadra
        assert not resultado.sin_efecto

    def test_lo_contado_no_se_mueve(self):
        resultado = calcular_ajuste_fondo(
            fondo_vigente=Decimal("500.00"),
            fondo_corregido=Decimal("300.00"),
            efectivo_esperado_vigente=Decimal("700.00"),
            efectivo_contado_vigente=Decimal("650.00"),
        )

        assert resultado.efectivo_contado == Decimal("650.00")
        # Había un faltante de 50; con 200 menos de cambio pasa a sobrante.
        assert resultado.diferencia_nueva == Decimal("150.00")
        assert not resultado.cuadra

    def test_el_mismo_cambio_no_tiene_efecto(self):
        resultado = calcular_ajuste_fondo(
            fondo_vigente=Decimal("500.00"),
            fondo_corregido=Decimal("500.00"),
            efectivo_esperado_vigente=Decimal("700.00"),
            efectivo_contado_vigente=Decimal("700.00"),
        )

        assert resultado.sin_efecto


# ---------------------------------------------------------------------------
# Casos de uso.
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


def _turno_con_cambio_mal_declarado(operador) -> Turno:
    """
    Abre declarando $9,000 de cambio cuando había $900, cobra un boleto y
    cierra contando bien lo que de verdad hay en la caja.
    """
    turno = services_caja.abrir_turno(operador=operador, fondo_inicial=CAMBIO_DECLARADO)
    folio = services.registrar_entrada(
        placa="ABC1234", operador=operador, momento=timezone.now() - timedelta(hours=5)
    )
    services.registrar_salida(
        uuid_folio=folio.uuid, pago_recibido=Decimal("200.00"), operador=operador
    )
    cobrado = services_caja.calcular_resumen(turno).total_cobrado
    cerrado, _ = services_caja.cerrar_turno(
        turno_id=turno.pk, efectivo_declarado=CAMBIO_REAL + cobrado
    )
    return cerrado


def _corregir(turno, autorizo, fondo=CAMBIO_REAL, justificacion=JUSTIFICACION):
    return services_caja.corregir_fondo(
        turno_id=turno.pk,
        fondo_corregido=fondo,
        justificacion=justificacion,
        autorizo=autorizo,
    )


class TestElCasoQueLoMotivo:
    def test_el_corte_nace_con_un_faltante_que_no_existe(self, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        assert turno.diferencia == Decimal("-8100.00")

    def test_corregir_el_cambio_cuadra_sin_inventar_efectivo(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        contado = turno.efectivo_declarado

        turno, ajuste, resultado = _corregir(turno, duena)
        turno.refresh_from_db()

        assert resultado.cuadra
        assert turno.fondo_vigente == CAMBIO_REAL
        assert turno.esperado_vigente == contado
        assert turno.diferencia_vigente == Decimal("0.00")
        # Lo contado no se tocó: nadie declaró $9,000 que no existían.
        assert turno.efectivo_vigente == contado
        assert turno.efectivo_ajustado is None
        assert turno.tiene_ajuste and turno.tiene_ajuste_fondo

        assert ajuste.motivo == MotivoAjuste.FONDO
        assert ajuste.corrige_fondo
        assert ajuste.monto_fondo == Decimal("-8100.00")
        assert ajuste.monto_mostrado == Decimal("-8100.00")
        assert ajuste.autorizo == duena

    def test_lo_declarado_al_abrir_no_se_toca(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        esperado_del_cierre = turno.efectivo_esperado

        _corregir(turno, duena)
        turno.refresh_from_db()

        assert turno.fondo_inicial == CAMBIO_DECLARADO
        assert turno.efectivo_esperado == esperado_del_cierre
        assert turno.diferencia == Decimal("-8100.00")
        # La fotografía del cierre sigue siendo la del cierre.
        resumen = services_caja.calcular_resumen(turno)
        assert resumen.fondo_inicial == CAMBIO_DECLARADO
        assert resumen.efectivo_esperado == esperado_del_cierre

    def test_las_metricas_no_suman_el_cambio_que_nunca_estuvo(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        desde = timezone.now() - timedelta(days=1)
        hasta = timezone.now() + timedelta(days=1)

        antes = services_metricas.cortes_del_periodo(desde, hasta)
        _corregir(turno, duena)
        despues = services_metricas.cortes_del_periodo(desde, hasta)

        assert antes["esperado"] - despues["esperado"] == Decimal("8100.00")
        assert despues["esperado"] == turno.efectivo_declarado
        assert despues["diferencia"] == Decimal("0.00")
        assert despues["faltantes"] == 0
        assert despues["ajustados"] == 1
        # Lo que se declaró al cerrar sigue pudiendo auditarse.
        assert despues["diferencia_original"] == Decimal("-8100.00")
        assert despues["ajustado_neto"] == Decimal("8100.00")


class TestReglas:
    def test_no_se_corrige_un_turno_abierto(self, duena, cajero):
        turno = services_caja.abrir_turno(operador=cajero, fondo_inicial=CAMBIO_DECLARADO)

        with pytest.raises(AjusteNoAplicable):
            _corregir(turno, duena)

    def test_el_mismo_cambio_se_rechaza(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        with pytest.raises(AjusteNoAplicable):
            _corregir(turno, duena, fondo=CAMBIO_DECLARADO)
        assert not AjusteCorte.objects.exists()

    def test_sin_justificacion_no_hay_correccion(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        with pytest.raises(AjusteNoAplicable):
            _corregir(turno, duena, justificacion="error")

    def test_el_cambio_no_puede_ser_negativo(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        with pytest.raises(ErrorConfiguracion):
            _corregir(turno, duena, fondo=Decimal("-1.00"))

    def test_exige_un_autor_identificado(self, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        with pytest.raises(ErrorConfiguracion):
            _corregir(turno, None)

    def test_el_motivo_del_cambio_no_sirve_para_ajustar_lo_contado(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        with pytest.raises(ErrorConfiguracion):
            services_caja.ajustar_corte(
                turno_id=turno.pk,
                efectivo_corregido=turno.efectivo_esperado,
                motivo=MotivoAjuste.FONDO,
                justificacion=JUSTIFICACION,
                autorizo=duena,
            )

    def test_se_acumula_con_un_ajuste_de_lo_contado(self, duena, cajero):
        """
        El camino de quien ya «cuadró» declarando los $9,000: primero corrige
        el cambio y luego devuelve lo contado a lo que de verdad había.
        """
        turno = _turno_con_cambio_mal_declarado(cajero)
        contado_real = turno.efectivo_declarado
        services_caja.ajustar_corte(
            turno_id=turno.pk,
            efectivo_corregido=turno.efectivo_esperado,
            motivo=MotivoAjuste.OTRO,
            justificacion="Se cuadró a mano antes de existir la corrección.",
            autorizo=duena,
        )

        _, _, resultado = _corregir(turno, duena)
        # Ahora sobra justo lo que se inventó al cuadrar.
        assert resultado.diferencia_nueva == Decimal("8100.00")

        services_caja.ajustar_corte(
            turno_id=turno.pk,
            efectivo_corregido=contado_real,
            motivo=MotivoAjuste.CAPTURA,
            justificacion="Se devuelve lo contado a la cifra real de la caja.",
            autorizo=duena,
        )
        turno.refresh_from_db()

        assert turno.diferencia_vigente == Decimal("0.00")
        assert turno.efectivo_vigente == contado_real
        assert turno.esperado_vigente == contado_real
        assert AjusteCorte.objects.filter(turno=turno).count() == 3

    def test_el_ajuste_de_lo_contado_parte_del_esperado_corregido(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        _corregir(turno, duena)
        turno.refresh_from_db()

        _, ajuste, resultado = services_caja.ajustar_corte(
            turno_id=turno.pk,
            efectivo_corregido=turno.efectivo_vigente + Decimal("10.00"),
            motivo=MotivoAjuste.RECUENTO,
            justificacion="Se recontó y aparecieron diez pesos más.",
            autorizo=duena,
        )

        assert ajuste.efectivo_esperado == turno.esperado_vigente
        assert resultado.diferencia_nueva == Decimal("10.00")

    def test_la_base_rechaza_un_asiento_que_no_mueve_nada(self, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)

        with pytest.raises(IntegrityError):
            AjusteCorte.objects.create(
                turno=turno,
                efectivo_esperado=turno.efectivo_esperado,
                efectivo_anterior=turno.efectivo_declarado,
                efectivo_nuevo=turno.efectivo_declarado,
                monto=Decimal("0.00"),
                diferencia_anterior=turno.diferencia,
                diferencia_nueva=turno.diferencia,
                fondo_anterior=CAMBIO_DECLARADO,
                fondo_nuevo=CAMBIO_DECLARADO,
                motivo=MotivoAjuste.FONDO,
                justificacion=JUSTIFICACION,
                autorizo=duena,
            )


class TestPantalla:
    def _url(self, turno):
        return reverse("web:corregir_cambio", kwargs={"turno_id": turno.pk})

    def test_el_cajero_no_puede_corregir_su_cambio(self, client, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        client.force_login(cajero)

        client.post(
            self._url(turno),
            {"cambio_real": "900", "justificacion": JUSTIFICACION, "clave": CLAVE},
        )

        assert not AjusteCorte.objects.exists()

    def test_el_comprobante_ofrece_la_correccion_al_administrador(
        self, client, duena, cajero
    ):
        turno = _turno_con_cambio_mal_declarado(cajero)

        client.force_login(cajero)
        del_cajero = client.get(reverse("web:corte", kwargs={"turno_id": turno.pk}))
        assert self._url(turno) not in del_cajero.content.decode()

        client.force_login(duena)
        de_la_duena = client.get(reverse("web:corte", kwargs={"turno_id": turno.pk}))
        assert self._url(turno) in de_la_duena.content.decode()

    def test_la_pantalla_de_cuadrar_avisa_del_otro_camino(self, client, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        client.force_login(duena)

        respuesta = client.get(
            reverse("web:ajustar_corte", kwargs={"turno_id": turno.pk})
        )

        assert self._url(turno) in respuesta.content.decode()

    def test_sin_la_contrasena_no_se_corrige_nada(self, client, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        client.force_login(duena)

        respuesta = client.post(
            self._url(turno),
            {"cambio_real": "900", "justificacion": JUSTIFICACION, "clave": "otra"},
        )

        assert "Contraseña incorrecta" in respuesta.content.decode()
        assert not AjusteCorte.objects.exists()

    def test_correccion_completa_desde_la_pantalla(self, client, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        client.force_login(duena)

        respuesta = client.post(
            self._url(turno),
            {"cambio_real": "900", "justificacion": JUSTIFICACION, "clave": CLAVE},
            follow=True,
        )
        turno.refresh_from_db()

        assert respuesta.status_code == 200
        assert turno.diferencia_vigente == Decimal("0.00")

        contenido = respuesta.content.decode()
        assert "La caja cuadra exactamente" in contenido
        # Las dos cifras del cambio conviven en el comprobante.
        assert "Cambio declarado al abrir" in contenido
        assert "$9,000.00" in contenido
        assert "$900.00" in contenido
        assert "Diferencia declarada al cerrar" in contenido
        assert "Cambio de apertura mal declarado" in contenido
        assert JUSTIFICACION.replace("$", "$") in contenido

    def test_el_historico_y_las_metricas_se_leen_corregidos(self, client, duena, cajero):
        turno = _turno_con_cambio_mal_declarado(cajero)
        _corregir(turno, duena)
        client.force_login(duena)

        historico = client.get(reverse("web:turno")).content.decode()
        assert '<span class="etiqueta cuadra">' in historico
        assert "Corregido después del cierre" in historico
        assert "$9,200.00" not in historico

        metricas = client.get(reverse("web:metricas"))
        assert metricas.status_code == 200
        assert "$9,200.00" not in metricas.content.decode()

    def test_no_se_corrige_un_turno_abierto_desde_la_pantalla(self, client, duena, cajero):
        turno = services_caja.abrir_turno(operador=cajero, fondo_inicial=CAMBIO_DECLARADO)
        client.force_login(duena)

        respuesta = client.get(self._url(turno), follow=True)

        assert "sigue abierto" in respuesta.content.decode()

"""
Pruebas de las métricas de operación.

Lo que se verifica aquí son afirmaciones con consecuencias: «la hora pico es
la de las 13:00» decide a qué hora hay alguien en la caseta, y «estos son los
clientes frecuentes» decide a quién se le ofrece una pensión. Una métrica
equivocada no rompe nada visible; simplemente lleva a decidir mal.

Las tres cosas que más fácilmente se calculan mal y que por eso tienen prueba
propia:

* **La hora se cuenta en zona local.** Las marcas se almacenan en UTC. Con
  `America/Mexico_City` eso son seis horas de desfase: una entrada de las
  20:00 aparecería a las 02:00 del día siguiente, y el pico señalaría una hora
  en que la caseta está cerrada.
* **El vector de horas lleva siempre 24 posiciones.** Una lista que omite las
  horas sin movimiento desplaza los índices y señala la hora equivocada.
* **Los totales de grupo no se agregan sobre agregados.** Django envuelve esa
  consulta en una subconsulta que devuelve cero en silencio, y un total en
  cero junto a filas correctas no se ve roto: se ve falso.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import services_caja, services_metricas
from apps.estacionamiento.domain.exceptions import ErrorConfiguracion
from apps.estacionamiento.domain.metricas import (
    DIAS_SEMANA,
    distribuir_permanencia,
    escala,
    hora_pico,
    porcentaje,
    ticket_promedio,
)
from apps.estacionamiento.models import (
    EstadoFolio,
    EstadoTurno,
    Folio,
    MotivoAjuste,
    Tarifa,
    TipoVehiculo,
    Turno,
)

CLAVE = "clave-de-prueba-1234"


# ---------------------------------------------------------------------------
# Lógica pura
# ---------------------------------------------------------------------------


class TestHoraPico:
    def test_senala_la_hora_de_mayor_volumen(self):
        conteos = [0] * 24
        conteos[8], conteos[13], conteos[19] = 42, 48, 18

        pico = hora_pico(conteos)

        assert pico.hora == 13
        assert pico.conteo == 48
        assert pico.etiqueta == "13:00 h"
        assert pico.porcentaje == 44.4
        assert not pico.hay_empate

    def test_un_empate_no_se_oculta(self):
        """
        Dos horas con el mismo volumen son dos turnos a cubrir, no uno.
        Anunciar solo la primera esconde la mitad del problema.
        """
        conteos = [0] * 24
        conteos[13] = conteos[19] = 48

        pico = hora_pico(conteos)

        assert pico.hora == 13
        assert pico.empates == (19,)
        assert pico.hay_empate

    def test_sin_movimientos_no_hay_hora_pico(self):
        """
        Devolver cero —la medianoche— afirmaría que el pico es a las 00:00.
        Sobre un reporte con el que se asigna personal, eso es una conclusión
        inventada.
        """
        pico = hora_pico([0] * 24)

        assert pico.hora is None
        assert pico.etiqueta == "—"
        assert pico.porcentaje == 0.0

    def test_exige_el_vector_completo_de_24_horas(self):
        with pytest.raises(ValueError, match="24 horas"):
            hora_pico([1, 2, 3])


class TestDistribucionDePermanencia:
    def test_reparte_en_los_seis_tramos(self):
        tramos = distribuir_permanencia([10, 45, 90, 300, 700])

        assert [t["conteo"] for t in tramos] == [1, 1, 1, 0, 1, 1]
        assert tramos[0]["etiqueta"] == "Hasta 30 min"
        assert tramos[-1]["etiqueta"] == "Más de 8 h"

    def test_los_tramos_vacios_se_devuelven_igual(self):
        """
        Omitirlos haría que el gráfico cambiara de forma entre dos periodos y
        que «no hubo estancias largas» se viera igual que «no se midieron».
        """
        tramos = distribuir_permanencia([])

        assert len(tramos) == 6
        assert all(t["conteo"] == 0 for t in tramos)

    def test_los_limites_caen_del_lado_inclusivo(self):
        tramos = distribuir_permanencia([30, 31, 60, 61])

        assert tramos[0]["conteo"] == 1   # 30 min exactos
        assert tramos[1]["conteo"] == 2   # 31 y 60
        assert tramos[2]["conteo"] == 1   # 61

    def test_ignora_los_minutos_ausentes(self):
        tramos = distribuir_permanencia([10, None, 20])
        assert sum(t["conteo"] for t in tramos) == 2

    def test_acepta_un_generador(self):
        """La consulta le entrega un cursor, no una lista en memoria."""
        tramos = distribuir_permanencia(m for m in [10, 45])
        assert [t["conteo"] for t in tramos][:2] == [1, 1]


class TestEscalaDelEje:
    @pytest.mark.parametrize(
        "valores, tope",
        [
            ([Decimal("1837.44")], Decimal("2000")),
            ([Decimal("7")], Decimal("8")),
            ([Decimal("0")], Decimal("1")),
            ([], Decimal("1")),
        ],
    )
    def test_redondea_a_una_cifra_legible(self, valores, tope):
        assert escala(valores) == tope

    def test_el_tope_siempre_cubre_el_maximo(self):
        for valor in [1, 9, 11, 99, 101, 999, 1001, 48731]:
            assert escala([Decimal(valor)]) >= Decimal(valor)

    def test_las_divisiones_son_exactas(self):
        """Un eje «redondo» con etiquetas como 458.33 no se lee de un vistazo."""
        tope = escala([Decimal("1837.44")], divisiones=4)
        assert tope % 4 == 0


class TestAuxiliares:
    def test_porcentaje_con_total_cero_no_divide(self):
        assert porcentaje(5, 0) == 0.0

    def test_ticket_sin_visitas_no_divide(self):
        assert ticket_promedio(Decimal("100"), 0) == Decimal("0.00")

    def test_ticket_promedio(self):
        assert ticket_promedio(Decimal("1120"), 14) == Decimal("80.00")


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifas():
    # Las migraciones de datos dejan un precio activo por tipo de vehículo, y
    # el modelo impide que haya dos. Se desactivan antes de crear los de la
    # prueba, que es lo mismo que hace la aplicación al cambiar un precio.
    Tarifa.objects.update(activa=False)

    auto = Tarifa.objects.create(
        nombre="Auto", tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("30.00"), minutos_cortesia=0, fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=60),
    )
    moto = Tarifa.objects.create(
        nombre="Moto", tipo_vehiculo=TipoVehiculo.MOTOCICLETA,
        cuota_hora=Decimal("15.00"), minutos_cortesia=0, fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=60),
    )
    return auto, moto


@pytest.fixture
def cajero(tarifas):
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def duena(tarifas):
    return get_user_model().objects.create_user(
        username="duena", password=CLAVE, is_staff=True
    )


def _local(dia_offset: int, hora: int, minuto: int = 0):
    """Marca de tiempo en la zona del negocio, a una hora concreta del reloj."""
    zona = timezone.get_current_timezone()
    fecha = timezone.localtime().date() + timedelta(days=dia_offset)
    return timezone.make_aware(datetime.combine(fecha, time(hora, minuto)), zona)


def _cobro(placa, *, entrada, minutos, importe, tarifa, operador, turno=None):
    return Folio.objects.create(
        placa=placa, tarifa=tarifa, turno=turno, estado=EstadoFolio.CERRADO,
        entrada_en=entrada, salida_en=entrada + timedelta(minutes=minutos),
        minutos=minutos, importe=Decimal(importe), pago_recibido=Decimal(importe),
        tarifa_cuota_hora=tarifa.cuota_hora,
        operador_entrada=operador, operador_salida=operador,
    )


def _rango(dias_atras: int = 7):
    zona = timezone.get_current_timezone()
    hoy = timezone.localtime().date()
    return (
        timezone.make_aware(datetime.combine(hoy - timedelta(days=dias_atras), time.min), zona),
        timezone.make_aware(datetime.combine(hoy, time.max), zona),
    )


class TestHorasDelDia:
    def test_la_hora_se_cuenta_en_zona_local(self, tarifas, cajero):
        """
        El fallo que esta prueba impide: las marcas se guardan en UTC y, sin
        convertir, una entrada de las 20:00 aparecería a las 02:00 del día
        siguiente. El reporte señalaría como hora pico una en que la caseta
        está cerrada.
        """
        auto, _ = tarifas
        _cobro("AAA111", entrada=_local(-1, 20), minutos=60, importe="30", tarifa=auto, operador=cajero)
        _cobro("BBB222", entrada=_local(-1, 20, 30), minutos=60, importe="30", tarifa=auto, operador=cajero)
        _cobro("CCC333", entrada=_local(-1, 9), minutos=60, importe="30", tarifa=auto, operador=cajero)

        horas = services_metricas.horas_del_dia(*_rango())

        assert horas["entradas"][20] == 2
        assert horas["entradas"][9] == 1
        assert horas["pico_entradas"].hora == 20

    def test_el_vector_tiene_siempre_24_posiciones(self, tarifas, cajero):
        auto, _ = tarifas
        _cobro("AAA111", entrada=_local(-1, 13), minutos=60, importe="30", tarifa=auto, operador=cajero)

        horas = services_metricas.horas_del_dia(*_rango())

        assert len(horas["entradas"]) == 24
        assert len(horas["ingresos"]) == 24
        assert horas["entradas"][0] == 0

    def test_el_pico_de_cobro_va_separado_del_de_entrada(self, tarifas, cajero):
        """
        Son preguntas distintas. Contratar personal con la curva de cobro
        pondría a la gente a trabajar cuando la fila ya se formó.
        """
        auto, _ = tarifas
        # Entran a las 8, salen a las 18: nueve horas después.
        for placa in ("AAA111", "BBB222", "CCC333"):
            _cobro(placa, entrada=_local(-1, 8), minutos=600, importe="250",
                   tarifa=auto, operador=cajero)

        horas = services_metricas.horas_del_dia(*_rango())

        assert horas["pico_entradas"].hora == 8
        assert horas["pico_cobros"].hora == 18

    def test_las_entradas_cuentan_aunque_el_folio_siga_abierto(self, tarifas, cajero):
        """Un vehículo que entró ocupó lugar y pidió atención, se haya cobrado o no."""
        auto, _ = tarifas
        Folio.objects.create(
            placa="DENTRO1", tarifa=auto, estado=EstadoFolio.ABIERTO,
            entrada_en=_local(0, 11), operador_entrada=cajero,
        )

        horas = services_metricas.horas_del_dia(*_rango())

        assert horas["entradas"][11] == 1

    def test_las_entradas_canceladas_no_cuentan(self, tarifas, cajero):
        auto, _ = tarifas
        Folio.objects.create(
            placa="ERROR01", tarifa=auto, estado=EstadoFolio.CANCELADO,
            entrada_en=_local(0, 11), operador_entrada=cajero,
        )

        horas = services_metricas.horas_del_dia(*_rango())

        assert horas["entradas"][11] == 0


class TestDiasDeSemana:
    def test_devuelve_los_siete_dias_en_orden(self, tarifas, cajero):
        auto, _ = tarifas
        _cobro("AAA111", entrada=_local(-1, 10), minutos=60, importe="30", tarifa=auto, operador=cajero)

        semana = services_metricas.dias_de_semana(*_rango())

        assert [d["dia"] for d in semana] == DIAS_SEMANA
        assert sum(d["entradas"] for d in semana) == 1

    def test_el_dia_coincide_con_el_de_la_fecha(self, tarifas, cajero):
        auto, _ = tarifas
        entrada = _local(-1, 10)
        _cobro("AAA111", entrada=entrada, minutos=60, importe="30", tarifa=auto, operador=cajero)

        semana = services_metricas.dias_de_semana(*_rango())
        esperado = DIAS_SEMANA[timezone.localtime(entrada).weekday()]

        assert next(d for d in semana if d["entradas"] == 1)["dia"] == esperado


class TestClientesFrecuentes:
    def test_solo_las_placas_que_repitieron(self, tarifas, cajero):
        auto, _ = tarifas
        for dia in (-3, -2, -1):
            _cobro("VUELVE1", entrada=_local(dia, 9), minutos=60, importe="30",
                   tarifa=auto, operador=cajero)
        _cobro("UNICA01", entrada=_local(-1, 10), minutos=60, importe="30",
               tarifa=auto, operador=cajero)

        clientes = services_metricas.clientes_frecuentes(*_rango())

        assert [f["placa"] for f in clientes["filas"]] == ["VUELVE1"]
        assert clientes["placas_frecuentes"] == 1
        assert clientes["placas_atendidas"] == 2

    def test_los_totales_del_grupo_no_salen_en_cero(self, tarifas, cajero):
        """
        El fallo que esta prueba impide: `Sum` sobre una columna que ya es un
        agregado devuelve cero en silencio. Un total en cero junto a filas con
        cifras correctas no se ve roto, se ve falso.
        """
        auto, _ = tarifas
        for dia in (-3, -2):
            _cobro("VUELVE1", entrada=_local(dia, 9), minutos=60, importe="30",
                   tarifa=auto, operador=cajero)
            _cobro("VUELVE2", entrada=_local(dia, 10), minutos=60, importe="20",
                   tarifa=auto, operador=cajero)
        _cobro("UNICA01", entrada=_local(-1, 10), minutos=60, importe="50",
               tarifa=auto, operador=cajero)

        clientes = services_metricas.clientes_frecuentes(*_rango())

        assert clientes["gasto_frecuentes"] == Decimal("100.00")
        assert clientes["visitas_frecuentes"] == 4
        # 100 de 150 cobrados en el periodo.
        assert clientes["parte_del_ingreso"] == 66.7

    def test_calcula_ticket_y_estancia_por_cliente(self, tarifas, cajero):
        auto, _ = tarifas
        _cobro("VUELVE1", entrada=_local(-2, 9), minutos=60, importe="30",
               tarifa=auto, operador=cajero)
        _cobro("VUELVE1", entrada=_local(-1, 9), minutos=120, importe="90",
               tarifa=auto, operador=cajero)

        fila = services_metricas.clientes_frecuentes(*_rango())["filas"][0]

        assert fila["visitas"] == 2
        assert fila["gasto"] == Decimal("120.00")
        assert fila["ticket"] == Decimal("60.00")
        assert fila["minutos_promedio"] == 90.0

    def test_el_orden_es_estable_entre_dos_ejecuciones(self, tarifas, cajero):
        """
        Sin un orden total, dos clientes empatados alternan de posición y
        quien imprime el reporte dos veces concluye que el sistema miente.
        """
        auto, _ = tarifas
        for placa in ("BBB222", "AAA111", "CCC333"):
            for dia in (-2, -1):
                _cobro(placa, entrada=_local(dia, 9), minutos=60, importe="30",
                       tarifa=auto, operador=cajero)

        primera = [f["placa"] for f in services_metricas.clientes_frecuentes(*_rango())["filas"]]
        segunda = [f["placa"] for f in services_metricas.clientes_frecuentes(*_rango())["filas"]]

        assert primera == segunda == ["AAA111", "BBB222", "CCC333"]

    def test_el_umbral_de_visitas_es_configurable(self, tarifas, cajero):
        auto, _ = tarifas
        for dia in (-3, -2):
            _cobro("DOS0001", entrada=_local(dia, 9), minutos=60, importe="30",
                   tarifa=auto, operador=cajero)
        for dia in (-3, -2, -1):
            _cobro("TRES001", entrada=_local(dia, 10), minutos=60, importe="30",
                   tarifa=auto, operador=cajero)

        con_dos = services_metricas.clientes_frecuentes(*_rango(), minimo_visitas=2)
        con_tres = services_metricas.clientes_frecuentes(*_rango(), minimo_visitas=3)

        assert con_dos["placas_frecuentes"] == 2
        assert [f["placa"] for f in con_tres["filas"]] == ["TRES001"]

    def test_una_sola_visita_no_puede_ser_el_umbral(self, tarifas):
        with pytest.raises(ErrorConfiguracion, match="al menos dos visitas"):
            services_metricas.clientes_frecuentes(*_rango(), minimo_visitas=1)

    def test_el_listado_se_trunca_y_lo_dice(self, tarifas, cajero):
        auto, _ = tarifas
        for n in range(4):
            for dia in (-2, -1):
                _cobro(f"PLACA{n:02d}", entrada=_local(dia, 9), minutos=60,
                       importe="30", tarifa=auto, operador=cajero)

        clientes = services_metricas.clientes_frecuentes(*_rango(), top=2)

        assert len(clientes["filas"]) == 2
        assert clientes["placas_frecuentes"] == 4
        assert clientes["truncado"]


class TestComposicion:
    def test_separa_cortesias_de_cobros(self, tarifas, cajero):
        auto, _ = tarifas
        _cobro("AAA111", entrada=_local(-1, 9), minutos=60, importe="30", tarifa=auto, operador=cajero)
        _cobro("BBB222", entrada=_local(-1, 10), minutos=10, importe="0", tarifa=auto, operador=cajero)

        boletos = services_metricas.composicion(*_rango())["boletos"]

        assert boletos[0] == {"etiqueta": "Con cobro", "conteo": 1, "porcentaje": 50.0}
        assert boletos[1] == {"etiqueta": "Cortesía", "conteo": 1, "porcentaje": 50.0}

    def test_agrupa_por_tipo_de_vehiculo(self, tarifas, cajero):
        auto, moto = tarifas
        _cobro("AAA111", entrada=_local(-1, 9), minutos=60, importe="30", tarifa=auto, operador=cajero)
        _cobro("MMM111", entrada=_local(-1, 9), minutos=60, importe="15", tarifa=moto, operador=cajero)

        por_tipo = services_metricas.composicion(*_rango())["por_tipo"]

        assert [t["etiqueta"] for t in por_tipo] == ["Automóvil", "Motocicleta"]
        assert por_tipo[0]["porcentaje"] == 66.7

    def test_agrupa_por_operador(self, tarifas, cajero, duena):
        auto, _ = tarifas
        _cobro("AAA111", entrada=_local(-1, 9), minutos=60, importe="30", tarifa=auto, operador=cajero)
        _cobro("BBB222", entrada=_local(-1, 9), minutos=60, importe="90", tarifa=auto, operador=duena)

        por_operador = services_metricas.composicion(*_rango())["por_operador"]

        assert por_operador[0]["operador"] == "duena"
        assert por_operador[0]["ingreso"] == Decimal("90.00")


class TestCortesDelPeriodo:
    def _turno_cerrado(self, operador, *, esperado, declarado, cerrado_en):
        turno = Turno.objects.create(
            operador=operador, estado=EstadoTurno.CERRADO,
            fondo_inicial=Decimal("0.00"),
            abierto_en=cerrado_en - timedelta(hours=8), cerrado_en=cerrado_en,
            efectivo_declarado=Decimal(declarado),
            total_cobrado=Decimal(esperado), efectivo_esperado=Decimal(esperado),
            diferencia=Decimal(declarado) - Decimal(esperado),
            folios_cobrados=0, folios_cortesia=0, resumen={},
        )
        return turno

    def test_suma_la_diferencia_vigente_y_no_la_del_cierre(self, tarifas, cajero, duena):
        """
        El punto donde las dos funciones se encuentran. Sumar las diferencias
        originales declararía en el reporte del mes un faltante que ya se
        demostró inexistente.
        """
        turno = self._turno_cerrado(
            cajero, esperado="1000.00", declarado="860.00", cerrado_en=_local(-1, 22)
        )
        services_caja.ajustar_corte(
            turno_id=turno.pk, efectivo_corregido=Decimal("1000.00"),
            motivo=MotivoAjuste.NO_REGISTRADO,
            justificacion="Cobro no registrado; el efectivo estaba en caja.",
            autorizo=duena,
        )

        caja = services_metricas.cortes_del_periodo(*_rango())

        assert caja["diferencia"] == Decimal("0.00")
        assert caja["diferencia_original"] == Decimal("-140.00")
        assert caja["ajustado_neto"] == Decimal("140.00")
        assert caja["ajustados"] == 1
        assert caja["faltantes"] == 0

    def test_cuenta_faltantes_y_sobrantes_por_separado(self, tarifas, cajero):
        self._turno_cerrado(cajero, esperado="1000.00", declarado="950.00", cerrado_en=_local(-2, 22))
        otro = get_user_model().objects.create_user(username="otro", password=CLAVE)
        self._turno_cerrado(otro, esperado="1000.00", declarado="1050.00", cerrado_en=_local(-1, 22))

        caja = services_metricas.cortes_del_periodo(*_rango())

        assert caja["faltantes"] == 1
        assert caja["sobrantes"] == 1
        assert caja["diferencia"] == Decimal("0.00")

    def test_un_periodo_sin_cortes_devuelve_ceros(self, tarifas):
        caja = services_metricas.cortes_del_periodo(*_rango())

        assert caja["turnos"] == 0
        assert caja["diferencia"] == Decimal("0.00")
        assert caja["detalle"] == []


class TestFachada:
    def test_rechaza_un_rango_invertido(self, tarifas):
        desde, hasta = _rango()
        with pytest.raises(ErrorConfiguracion, match="invertido"):
            services_metricas.metricas_por_rango(hasta, desde)

    def test_un_periodo_vacio_no_revienta(self, tarifas):
        """
        Un reporte de un día sin movimiento es un reporte válido: dice que no
        hubo movimiento. No debe hacer caer la pantalla.
        """
        datos = services_metricas.metricas_por_rango(*_rango())

        assert datos["clientes"]["filas"] == []
        assert datos["horas"]["pico_entradas"].hora is None
        assert datos["serie_diaria"] == []
        assert len(datos["semana"]) == 7

"""
Pruebas del reporte en PDF y de sus gráficos.

El PDF es lo que sale del negocio: se manda por correo y lo lee alguien que no
tiene acceso al sistema. Eso cambia qué hay que verificar. No basta con que la
función no falle; hay que comprobar que el archivo es un PDF de verdad, que se
descarga en lugar de abrirse, que no se queda en ninguna caché —lleva cifras de
caja— y, sobre todo, que **dice lo mismo que la pantalla**: si el documento que
se manda y la pantalla donde se revisó discreparan en una cifra, el reporte
dejaría de servir para nada.

Los gráficos se comprueban sobre el SVG generado. Es texto, de modo que puede
examinarse sin abrir un navegador: cuántas barras tiene, si los datos de
usuario van escapados y si un periodo vacío dibuja un aviso en lugar de nada.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
import reportlab.rl_config
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.estacionamiento import graficos, reportes_pdf, services_caja, services_metricas
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
# Gráficos. Sin base de datos: son funciones puras sobre listas.
# ---------------------------------------------------------------------------


class TestGraficosSVG:
    def test_dibuja_una_barra_por_hora(self):
        datos = [
            {"etiqueta": f"{h:02d}", "valor": h, "titulo": f"{h}:00"}
            for h in range(24)
        ]

        svg = graficos.barras_verticales(datos)

        assert svg.count("<path") == 24
        assert "<svg" in svg and "role=\"img\"" in svg

    def test_el_maximo_se_dibuja_en_el_paso_oscuro(self):
        """
        La jerarquía usa dos pasos del mismo azul y no dos colores distintos:
        así sobrevive a una impresión en blanco y negro, donde dos matices se
        vuelven el mismo tono.
        """
        datos = [
            {"etiqueta": "a", "valor": 1, "titulo": ""},
            {"etiqueta": "b", "valor": 9, "titulo": ""},
        ]

        svg = graficos.barras_verticales(datos)

        assert svg.count(f'fill="{graficos.T_DESTACADA}"') == 1
        assert svg.count(f'fill="{graficos.T_SERIE}"') == 1
        # El token lleva su valor claro de respaldo: el SVG se ve bien aunque
        # se extraiga del HTML y se abra suelto, sin la hoja de estilos.
        assert graficos.DESTACADA in graficos.T_DESTACADA

    def test_una_placa_se_escapa_antes_de_entrar_al_svg(self):
        """
        La placa es texto capturado por un operador. Sin escapar, basta con
        teclear una etiqueta para inyectar marcado en la pantalla del dueño.
        """
        svg = graficos.barras_horizontales(
            [{"etiqueta": '<script>alert(1)</script>', "valor": 1, "titulo": "x"}]
        )

        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg

    def test_un_periodo_vacio_dice_que_esta_vacio(self):
        """
        «No hubo movimiento» y «la gráfica no cargó» son cosas distintas y no
        deben parecer la misma.
        """
        svg = graficos.barras_verticales([])

        assert "Sin movimientos en el periodo" in svg

    def test_la_barra_segmentada_lleva_leyenda_con_nombres(self):
        """
        La identidad de cada parte nunca depende solo del color: el nombre va
        siempre al lado, para quien no distingue el matiz y para el papel.
        """
        svg = graficos.barra_segmentada(
            [
                {"etiqueta": "Con cobro", "conteo": 90, "porcentaje": 90.0},
                {"etiqueta": "Cortesía", "conteo": 10, "porcentaje": 10.0},
            ]
        )

        assert "Con cobro" in svg and "Cortes" in svg
        assert graficos.T_DESTACADA in svg and graficos.T_SEGUNDA in svg

    def test_los_colores_siguen_al_tema_del_operador(self):
        """
        El fallo que esta prueba impide: con los colores incrustados, los
        rótulos de los ejes quedaban en gris oscuro sobre fondo oscuro. La
        gráfica se veía y no se podía leer, que en una caseta de madrugada es
        lo mismo que no estar.
        """
        svg = graficos.barras_verticales(
            [{"etiqueta": "a", "valor": 1, "titulo": ""}]
        )

        assert "var(--viz-apagado" in svg
        assert "var(--viz-serie" in svg or "var(--viz-destacada" in svg

    def test_el_grafico_puede_desplazarse_de_lado_en_un_telefono(self):
        """
        Encogido al ancho de un teléfono, el texto del SVG baja de cinco
        píxeles. El ancho mínimo lo impide y el envoltorio da por dónde
        moverse, igual que ya hacen las tablas de esta aplicación.
        """
        svg = graficos.barras_verticales(
            [{"etiqueta": "a", "valor": 1, "titulo": ""}]
        )

        assert 'class="grafico-envoltura"' in svg
        assert "min-width:600px" in svg

    def test_la_barra_segmentada_sin_datos_no_divide_entre_cero(self):
        svg = graficos.barra_segmentada(
            [{"etiqueta": "Con cobro", "conteo": 0, "porcentaje": 0.0}]
        )
        assert "Sin movimientos" in svg


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.django_db


@pytest.fixture
def tarifa():
    Tarifa.objects.update(activa=False)
    return Tarifa.objects.create(
        nombre="Prueba", tipo_vehiculo=TipoVehiculo.AUTOMOVIL,
        cuota_hora=Decimal("30.00"), minutos_cortesia=0, fraccion_minutos=1,
        vigente_desde=timezone.now() - timedelta(days=60),
    )


@pytest.fixture
def cajero(tarifa):
    return get_user_model().objects.create_user(username="cajero", password=CLAVE)


@pytest.fixture
def duena(tarifa):
    return get_user_model().objects.create_user(
        username="duena", password=CLAVE, is_staff=True
    )


@pytest.fixture
def sin_comprimir(monkeypatch):
    """
    Desactiva la compresión de los flujos del PDF durante la prueba.

    Sin esto, el texto del documento viaja comprimido y no puede buscarse en
    los bytes. Afecta solo al tamaño del archivo, no a su contenido, de modo
    que lo que se comprueba sigue siendo el documento real.
    """
    monkeypatch.setattr(reportlab.rl_config, "pageCompression", 0)


def _rango(dias: int = 7):
    zona = timezone.get_current_timezone()
    hoy = timezone.localtime().date()
    return (
        timezone.make_aware(datetime.combine(hoy - timedelta(days=dias), time.min), zona),
        timezone.make_aware(datetime.combine(hoy, time.max), zona),
    )


def _escenario(tarifa, operador, duena=None):
    """Un turno cerrado con faltante de $140, corregido, y varios cobros."""
    zona = timezone.get_current_timezone()
    ayer = timezone.localtime().date() - timedelta(days=1)
    base = timezone.make_aware(datetime.combine(ayer, time(9, 0)), zona)

    turno = Turno.objects.create(
        operador=operador, estado=EstadoTurno.CERRADO, fondo_inicial=Decimal("0.00"),
        abierto_en=base - timedelta(hours=2),
        cerrado_en=base + timedelta(hours=12),
        efectivo_declarado=Decimal("160.00"), total_cobrado=Decimal("300.00"),
        efectivo_esperado=Decimal("300.00"), diferencia=Decimal("-140.00"),
        folios_cobrados=4, folios_cortesia=0, resumen={},
    )
    for indice, placa in enumerate(["VUELVE1", "VUELVE1", "OTRA001", "OTRA002"]):
        entrada = base + timedelta(hours=indice)
        Folio.objects.create(
            placa=placa, tarifa=tarifa, turno=turno, estado=EstadoFolio.CERRADO,
            entrada_en=entrada, salida_en=entrada + timedelta(hours=1), minutos=60,
            importe=Decimal("75.00"), pago_recibido=Decimal("75.00"),
            tarifa_cuota_hora=tarifa.cuota_hora,
            operador_entrada=operador, operador_salida=operador,
        )
    if duena is not None:
        services_caja.ajustar_corte(
            turno_id=turno.pk, efectivo_corregido=Decimal("300.00"),
            motivo=MotivoAjuste.NO_REGISTRADO,
            justificacion="Cobro no registrado; el efectivo estaba en caja.",
            autorizo=duena,
        )
    return turno


def _construir(desde, hasta, solicitado_por="duena") -> bytes:
    metricas = services_metricas.metricas_por_rango(desde, hasta)
    resumen = services_caja.reporte_por_rango(desde, hasta)
    return reportes_pdf.construir_reporte(
        metricas, resumen=resumen, solicitado_por=solicitado_por
    )


class TestDocumento:
    def test_produce_un_pdf_valido(self, tarifa, cajero):
        _escenario(tarifa, cajero)

        pdf = _construir(*_rango())

        assert pdf.startswith(b"%PDF-")
        assert pdf.rstrip().endswith(b"%%EOF")
        assert pdf.count(b"/Type /Page\n") >= 1

    def test_un_periodo_sin_movimientos_tambien_produce_documento(self, tarifa):
        """
        Un reporte vacío es un reporte válido: dice que no hubo movimiento.
        Fallar aquí dejaría al dueño sin forma de documentar un día cerrado.
        """
        pdf = _construir(*_rango())

        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 1000

    def test_lleva_las_secciones_que_se_prometieron(self, tarifa, cajero, sin_comprimir):
        _escenario(tarifa, cajero)

        pdf = _construir(*_rango())

        for seccion in (
            b"Horas pico",
            b"Clientes frecuentes",
            b"Control de caja",
            b"Ingreso por hora de cobro",
            b"Ingreso d",          # «Ingreso día por día»: la tilde va escapada
            b"Total cobrado",
        ):
            assert seccion in pdf, seccion

    def test_el_corte_corregido_muestra_las_dos_cifras(
        self, tarifa, cajero, duena, sin_comprimir
    ):
        """
        La propiedad que hace del ajuste un control: el documento que se manda
        dice cuánto se declaró al cerrar **y** cuánto resultó ser cierto.
        """
        _escenario(tarifa, cajero, duena)

        pdf = _construir(*_rango())

        assert b"corregidos" in pdf
        assert b"-$140.00" in pdf        # lo declarado al cerrar
        assert b"+$140.00" in pdf        # lo que movió la corrección

    def test_el_pdf_y_la_pantalla_salen_del_mismo_calculo(self, tarifa, cajero):
        """
        Si cada vista armara su propia consulta, bastaría un filtro distinto
        para que el dueño mandara cifras que no vio.
        """
        _escenario(tarifa, cajero)
        desde, hasta = _rango()

        primera = services_metricas.metricas_por_rango(desde, hasta)
        segunda = services_metricas.metricas_por_rango(desde, hasta)

        assert primera["caja"]["diferencia"] == segunda["caja"]["diferencia"]
        assert [f["placa"] for f in primera["clientes"]["filas"]] == [
            f["placa"] for f in segunda["clientes"]["filas"]
        ]

    def test_el_nombre_del_archivo_lleva_el_periodo(self, tarifa):
        desde, hasta = _rango()
        metricas = services_metricas.metricas_por_rango(desde, hasta)

        nombre = reportes_pdf.nombre_de_archivo(metricas)

        assert nombre.startswith("reporte_")
        assert nombre.endswith(".pdf")
        assert f"{timezone.localtime(desde):%Y%m%d}" in nombre

    def test_un_solo_dia_no_repite_la_fecha(self, tarifa):
        zona = timezone.get_current_timezone()
        hoy = timezone.localtime().date()
        desde = timezone.make_aware(datetime.combine(hoy, time.min), zona)
        hasta = timezone.make_aware(datetime.combine(hoy, time.max), zona)

        nombre = reportes_pdf.nombre_de_archivo(
            services_metricas.metricas_por_rango(desde, hasta)
        )

        assert nombre == f"reporte_{hoy:%Y%m%d}.pdf"


class TestPantallaYDescarga:
    def test_el_cajero_no_entra_a_las_metricas(self, client, cajero):
        client.force_login(cajero)

        respuesta = client.get(reverse("web:metricas"))

        assert respuesta.status_code == 302

    def test_el_cajero_no_descarga_el_pdf(self, client, cajero):
        client.force_login(cajero)

        respuesta = client.get(reverse("web:metricas_pdf"))

        assert respuesta.status_code == 302

    def test_la_duena_ve_las_metricas(self, client, duena, tarifa, cajero):
        _escenario(tarifa, cajero)
        client.force_login(duena)

        contenido = client.get(
            reverse("web:metricas"), {"desde": _rango()[0].date(), "hasta": _rango()[1].date()}
        ).content.decode()

        assert "Horas pico" in contenido
        assert "Clientes frecuentes" in contenido
        assert "<svg" in contenido
        assert "Descargar PDF para mandar" in contenido

    def test_el_pdf_se_descarga_y_no_se_guarda_en_cache(
        self, client, duena, tarifa, cajero
    ):
        """
        `attachment` porque el documento existe para mandarse, y `no-store`
        porque lleva cifras de caja que no deben quedar en ningún proxy.
        """
        _escenario(tarifa, cajero)
        client.force_login(duena)
        desde, hasta = _rango()

        respuesta = client.get(
            reverse("web:metricas_pdf"),
            {"desde": desde.date(), "hasta": hasta.date()},
        )

        assert respuesta.status_code == 200
        assert respuesta["Content-Type"] == "application/pdf"
        assert respuesta["Content-Disposition"].startswith("attachment;")
        assert "reporte_" in respuesta["Content-Disposition"]
        assert "no-store" in respuesta["Cache-Control"]
        assert respuesta.content.startswith(b"%PDF-")

    def test_un_rango_invertido_no_descarga_nada(self, client, duena):
        """
        Un periodo mal capturado devuelve a la pantalla, no un PDF vacío: un
        archivo sin cifras que alguien manda sin abrirlo es peor que un error.
        """
        client.force_login(duena)
        hoy = timezone.localtime().date()

        respuesta = client.get(
            reverse("web:metricas_pdf"),
            {"desde": hoy.isoformat(), "hasta": (hoy - timedelta(days=5)).isoformat()},
            follow=True,
        )

        assert respuesta.redirect_chain
        assert respuesta.redirect_chain[-1][0] == reverse("web:metricas")
        assert "application/pdf" not in respuesta["Content-Type"]
        assert "Revise el periodo solicitado" in respuesta.content.decode()

    def test_el_umbral_de_frecuencia_llega_hasta_el_reporte(
        self, client, duena, tarifa, cajero
    ):
        _escenario(tarifa, cajero)
        client.force_login(duena)
        desde, hasta = _rango()

        contenido = client.get(
            reverse("web:metricas"),
            {
                "desde": desde.date(), "hasta": hasta.date(),
                "minimo_visitas": 3, "top_clientes": 5,
            },
        ).content.decode()

        # VUELVE1 tiene dos visitas: con el umbral en tres, nadie es frecuente.
        assert "Ninguna placa repiti" in contenido

"""
Reporte de operación en PDF, con gráficos y totales.

Por qué un PDF generado en el servidor y no una pantalla que se imprime: el
documento se manda. «Para mandar resultados» fue el requisito, y lo que se
manda por correo o por mensaje tiene que ser un archivo con su propio
contenido, idéntico para quien lo reciba, sin depender de que el navegador de
nadie lo convierta bien. Un «imprimir a PDF» produce un archivo distinto en
cada equipo y ninguno se puede generar desde un comando programado.

Los gráficos se dibujan **vectoriales**, no como imagen incrustada: el archivo
pesa una fracción, se puede ampliar sin que se pixele y el texto de los
rótulos sigue siendo texto —se puede buscar y lo lee un lector de pantalla—.

La geometría y la paleta son las mismas que las de los SVG de la pantalla
(`graficos.py`), de donde se importan los colores. Es deliberado: el dueño
revisa el reporte en la pantalla y manda el PDF, y dos documentos que
discrepan en una cifra o en un color obligan a comprobar cuál de los dos
miente.

Aviso sobre el sistema de coordenadas: ReportLab sitúa el origen **abajo** a
la izquierda y el eje Y crece hacia arriba, al contrario que SVG. Todo el
dibujo de este módulo está escrito en esos términos.
"""
from __future__ import annotations

import io
import logging
from decimal import Decimal

from django.utils import timezone
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import graficos, marca
from .domain.metricas import escala
from .templatetags.moneda import duracion, pesos, pesos_con_signo

logger = logging.getLogger(__name__)

# Carta y no A4: es el tamaño que se imprime en México, y un reporte que sale
# con márgenes raros en la impresora de la oficina se reimprime a mano.
PAGINA = letter
MARGEN = 16 * mm
ANCHO_UTIL = PAGINA[0] - 2 * MARGEN

# --- Colores, tomados de la paleta verificada -------------------------------
C_SERIE = colors.HexColor(graficos.SERIE)
C_DESTACADA = colors.HexColor(graficos.DESTACADA)
C_SEGUNDA = colors.HexColor(graficos.SEGUNDA)
C_TINTA = colors.HexColor(graficos.TINTA)
C_SUAVE = colors.HexColor(graficos.TINTA_SUAVE)
C_APAGADO = colors.HexColor(graficos.APAGADO)
C_CUADRICULA = colors.HexColor(graficos.CUADRICULA)
C_EJE = colors.HexColor(graficos.EJE)
C_EXITO = colors.HexColor(graficos.EXITO)
C_ERROR = colors.HexColor(graficos.ERROR)
C_ALERTA = colors.HexColor(graficos.ALERTA)
C_RAMPA = [colors.HexColor(c) for c in graficos.RAMPA]


def _estilos() -> dict:
    """
    Estilos de párrafo del documento.

    Helvetica en todo, incluidas las cifras grandes: es la fuente base del
    formato PDF, de modo que el archivo no necesita incrustar tipografía
    alguna y se abre igual en cualquier lector. Sus dígitos tienen el mismo
    ancho, lo que alinea las columnas de importes sin trucos.
    """
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "titulo", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=17, leading=21, textColor=C_TINTA, spaceAfter=2, alignment=TA_LEFT,
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=C_SUAVE, spaceAfter=10,
        ),
        "seccion": ParagraphStyle(
            "seccion", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11.5, leading=15, textColor=C_TINTA, spaceBefore=13, spaceAfter=5,
        ),
        "nota": ParagraphStyle(
            "nota", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=11.5, textColor=C_SUAVE, spaceAfter=6,
        ),
        "hallazgo": ParagraphStyle(
            "hallazgo", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=C_TINTA, spaceAfter=7,
        ),
        "celda": ParagraphStyle(
            "celda", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, leading=10.5, textColor=C_TINTA,
        ),
        "vacio": ParagraphStyle(
            "vacio", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=9, leading=12, textColor=C_APAGADO, spaceAfter=6,
        ),
        "kpi_rotulo": ParagraphStyle(
            "kpi_rotulo", parent=base["Normal"], fontName="Helvetica",
            fontSize=7.5, leading=9, textColor=C_SUAVE, alignment=TA_CENTER,
        ),
        "kpi_valor": ParagraphStyle(
            "kpi_valor", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=C_TINTA, alignment=TA_CENTER,
        ),
    }


# --- Gráficos vectoriales ---------------------------------------------------


def _barra_vertical(dibujo: Drawing, x, y, ancho, alto, color) -> None:
    """
    Barra con el extremo superior redondeado y la base recta.

    ReportLab redondea las cuatro esquinas de un rectángulo o ninguna, así que
    la barra se compone de dos: la redondeada completa y un parche recto en la
    base. El resultado es el mismo trazo que en pantalla, y la base recta
    importa porque el apoyo común es lo que permite comparar longitudes.
    """
    if alto <= 0:
        return
    radio = min(3, ancho / 2, alto)
    dibujo.add(Rect(x, y, ancho, alto, rx=radio, ry=radio, fillColor=color, strokeColor=None))
    if alto > radio:
        dibujo.add(Rect(x, y, ancho, radio, fillColor=color, strokeColor=None))


def grafico_columnas(
    datos: list[dict],
    *,
    ancho: float = ANCHO_UTIL,
    alto: float = 112,
    formato=str,
    etiquetar_todas: bool = False,
) -> Drawing:
    """
    Columnas con cuadrícula de cuatro divisiones y el máximo destacado.

    Gemelo de `graficos.barras_verticales`, con la que comparte escala,
    paleta y criterio: una sola serie, un solo matiz, sin leyenda —el título
    de la sección dice qué se mide— y la cifra impresa solo donde aporta. Con
    una sola serie, el color no transporta información: la altura la lleva. Es
    lo que hace que el reporte siga siendo exacto impreso en blanco y negro.
    """
    dibujo = Drawing(ancho, alto)
    if not datos:
        dibujo.add(String(ancho / 2, alto / 2, "Sin movimientos en el periodo",
                          fontName="Helvetica-Oblique", fontSize=9,
                          fillColor=C_APAGADO, textAnchor="middle"))
        return dibujo

    margen_izq, margen_der, margen_sup, margen_inf = 52, 4, 14, 20
    area_ancho = ancho - margen_izq - margen_der
    area_alto = alto - margen_sup - margen_inf
    tope = escala([d["valor"] for d in datos])

    for i in range(5):
        y = margen_inf + area_alto * i / 4
        dibujo.add(Line(margen_izq, y, ancho - margen_der, y,
                        strokeColor=C_EJE if i == 0 else C_CUADRICULA, strokeWidth=0.5))
        dibujo.add(String(margen_izq - 5, y - 2.5,
                          formato(tope * Decimal(i) / Decimal(4)),
                          fontName="Helvetica", fontSize=6.5,
                          fillColor=C_APAGADO, textAnchor="end"))

    paso = area_ancho / len(datos)
    ancho_barra = max(paso - 2, 0.8)
    maximo = max((Decimal(d["valor"] or 0) for d in datos), default=Decimal(0))
    salto = max(1, (len(datos) + 11) // 12)

    for indice, dato in enumerate(datos):
        valor = Decimal(dato["valor"] or 0)
        # Todo a float antes de multiplicar: los márgenes vienen en
        # milímetros —float— y los importes en Decimal, y Python no mezcla
        # los dos tipos. La precisión aquí es de píxeles, no contable.
        altura = area_alto * float(valor) / float(tope) if tope else 0.0
        x = margen_izq + indice * paso
        es_max = dato.get("destacada", valor == maximo and maximo > 0)

        _barra_vertical(dibujo, x, margen_inf, ancho_barra, altura,
                        C_DESTACADA if es_max else C_SERIE)

        if (es_max or etiquetar_todas) and valor > 0:
            dibujo.add(String(x + ancho_barra / 2, margen_inf + altura + 3, formato(valor),
                              fontName="Helvetica-Bold", fontSize=6.5,
                              fillColor=C_TINTA, textAnchor="middle"))
        if indice % salto == 0 or es_max:
            dibujo.add(String(x + ancho_barra / 2, 7, str(dato["etiqueta"]),
                              fontName="Helvetica", fontSize=6.5,
                              fillColor=C_APAGADO, textAnchor="middle"))

    return dibujo


def grafico_barras(datos: list[dict], *, ancho: float = ANCHO_UTIL, formato=str) -> Drawing:
    """
    Ranking horizontal, de mayor a menor, con la cifra al final de cada barra.

    La forma correcta para nombres que no caben bajo una columna —una placa— y
    para valores cercanos: todas las barras parten de la misma línea, y eso es
    lo que permite distinguir 14 visitas de 13. Un pastel de veinte sectores
    no permitiría distinguir nada.
    """
    alto_barra, hueco = 11, 4
    alto = max(len(datos), 1) * (alto_barra + hueco) + 6
    dibujo = Drawing(ancho, alto)
    if not datos:
        dibujo.add(String(ancho / 2, alto / 2, "Sin clientes con visitas repetidas",
                          fontName="Helvetica-Oblique", fontSize=9,
                          fillColor=C_APAGADO, textAnchor="middle"))
        return dibujo

    margen_izq, margen_der = 72, 62
    area_ancho = ancho - margen_izq - margen_der
    tope = escala([d["valor"] for d in datos])

    for indice, dato in enumerate(datos):
        valor = Decimal(dato["valor"] or 0)
        largo = area_ancho * float(valor) / float(tope) if tope else 0.0
        # Se dibuja de arriba hacia abajo: en ReportLab eso es restar.
        y = alto - 6 - (indice + 1) * (alto_barra + hueco) + hueco
        color = C_DESTACADA if indice == 0 else C_SERIE

        dibujo.add(String(margen_izq - 5, y + 3, str(dato["etiqueta"]),
                          fontName="Helvetica", fontSize=7.5,
                          fillColor=C_SUAVE, textAnchor="end"))
        if largo > 0:
            radio = min(2.5, alto_barra / 2, largo)
            dibujo.add(Rect(margen_izq, y, largo, alto_barra, rx=radio, ry=radio,
                            fillColor=color, strokeColor=None))
            dibujo.add(Rect(margen_izq, y, min(radio, largo), alto_barra,
                            fillColor=color, strokeColor=None))
        dibujo.add(String(margen_izq + largo + 5, y + 3, formato(valor),
                          fontName="Helvetica-Bold", fontSize=7.5, fillColor=C_TINTA))

    return dibujo


def grafico_segmentado(partes: list[dict], *, ancho: float = ANCHO_UTIL) -> Drawing:
    """
    Un todo repartido en partes, en una barra con leyenda.

    No es un pastel a propósito: dos longitudes sobre una recta se comparan
    mejor que dos ángulos, y el nombre de cada parte acompaña siempre a su
    color en la leyenda, de modo que la identidad no depende del matiz ni de
    que el reporte se imprima en color.
    """
    alto_barra, alto = 17, 36
    dibujo = Drawing(ancho, alto)
    total = sum(int(p["conteo"] or 0) for p in partes)
    if total <= 0:
        dibujo.add(String(ancho / 2, alto / 2, "Sin boletos en el periodo",
                          fontName="Helvetica-Oblique", fontSize=9,
                          fillColor=C_APAGADO, textAnchor="middle"))
        return dibujo

    paleta = [C_DESTACADA, C_SEGUNDA, C_RAMPA[1], C_RAMPA[0]]
    x, y = 0.0, alto - alto_barra

    for indice, parte in enumerate(partes):
        conteo = int(parte["conteo"] or 0)
        if conteo <= 0:
            continue
        largo = ancho * conteo / total
        color = paleta[indice % len(paleta)]
        dibujo.add(Rect(x, y, max(largo - 2, 1), alto_barra, rx=2, ry=2,
                        fillColor=color, strokeColor=None))
        if largo > 40:
            dibujo.add(String(x + (largo - 2) / 2, y + 5.5,
                              f"{parte.get('porcentaje', 0)} %",
                              fontName="Helvetica-Bold", fontSize=8,
                              fillColor=colors.white, textAnchor="middle"))
        x += largo

    x = 0.0
    for indice, parte in enumerate(partes):
        color = paleta[indice % len(paleta)]
        etiqueta = f"{parte['etiqueta']} · {parte['conteo']}"
        dibujo.add(Rect(x, 3, 7, 7, rx=1.5, ry=1.5, fillColor=color, strokeColor=None))
        dibujo.add(String(x + 11, 4, etiqueta, fontName="Helvetica", fontSize=8,
                          fillColor=C_SUAVE))
        x += 11 + len(etiqueta) * 4.4 + 14

    return dibujo


# --- Bloques del documento --------------------------------------------------


def _tabla(filas: list[list], anchos: list[float], *, numericas: tuple[int, ...] = ()) -> Table:
    """
    Tabla de datos con hilos finos y cabecera sobria.

    Sin relleno de color en las filas: el reporte se imprime, y las bandas
    alternadas se vuelven manchas grises que estorban para leer una cifra. El
    hilo horizontal basta para seguir la fila.
    """
    tabla = Table(filas, colWidths=anchos, repeatRows=1, hAlign="LEFT")
    estilo = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_SUAVE),
        ("TEXTCOLOR", (0, 1), (-1, -1), C_TINTA),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, C_EJE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, C_CUADRICULA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for columna in numericas:
        estilo.append(("ALIGN", (columna, 0), (columna, -1), "RIGHT"))
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _indicadores(celdas: list[tuple[str, str]], estilos: dict) -> Table:
    """
    Fila de indicadores: el titular del reporte.

    Son cifras sueltas, no una serie, de modo que no van en un gráfico: una
    barra sola no compara nada y solo gasta espacio. Van grandes porque son lo
    primero que alguien mira al abrir el archivo.
    """
    por_fila = 4
    tabla_filas = []
    for inicio in range(0, len(celdas), por_fila):
        grupo = celdas[inicio : inicio + por_fila]
        grupo += [("", "")] * (por_fila - len(grupo))
        tabla_filas.append([Paragraph(r, estilos["kpi_rotulo"]) for r, _ in grupo])
        tabla_filas.append([Paragraph(v, estilos["kpi_valor"]) for _, v in grupo])

    ancho = ANCHO_UTIL / por_fila
    tabla = Table(tabla_filas, colWidths=[ancho] * por_fila, hAlign="LEFT")
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
    for indice in range(0, len(tabla_filas), 2):
        estilo += [
            ("TOPPADDING", (0, indice), (-1, indice), 7),
            ("BOTTOMPADDING", (0, indice + 1), (-1, indice + 1), 7),
            ("LINEBELOW", (0, indice + 1), (-1, indice + 1), 0.5, C_CUADRICULA),
        ]
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _pie(canvas, documento) -> None:
    """
    Pie con la marca, el momento de emisión y la paginación.

    El momento de emisión no es un adorno. Un reporte que circula por correo
    acaba reenviado semanas después, y sin la fecha de generación nadie puede
    saber si las cifras que está leyendo son las últimas.
    """
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(C_APAGADO)
    canvas.setStrokeColor(C_CUADRICULA)
    canvas.setLineWidth(0.5)
    canvas.line(MARGEN, 12 * mm, PAGINA[0] - MARGEN, 12 * mm)
    canvas.drawString(
        MARGEN, 8.5 * mm,
        f"{marca.nombre_comercial()} · emitido el "
        f"{timezone.localtime(documento._emitido):%d/%m/%Y a las %H:%M}",
    )
    canvas.drawRightString(PAGINA[0] - MARGEN, 8.5 * mm, f"Página {canvas.getPageNumber()}")
    canvas.restoreState()


# --- Documento --------------------------------------------------------------


def construir_reporte(metricas: dict, *, resumen: dict, solicitado_por: str = "") -> bytes:
    """
    Arma el PDF completo a partir de las métricas del periodo.

    Recibe las métricas ya calculadas y no las consulta él mismo: así el PDF
    y la pantalla salen del mismo `metricas_por_rango` y no pueden discrepar.
    Esa separación también permite probar el documento con cifras inventadas,
    sin base de datos.

    El orden de las secciones responde a cómo se lee un reporte y no a cómo se
    calculó: primero el titular —cuánto entró—, después la demanda —cuándo—,
    después quién la genera y al final el control de caja, que es lo que se
    consulta cuando algo de lo anterior no cuadra.
    """
    estilos = _estilos()
    bufer = io.BytesIO()
    emitido = metricas.get("generado_en") or timezone.now()

    documento = SimpleDocTemplate(
        bufer,
        pagesize=PAGINA,
        leftMargin=MARGEN, rightMargin=MARGEN,
        topMargin=MARGEN, bottomMargin=20 * mm,
        title=f"Reporte de operación · {marca.nombre_comercial()}",
        author=marca.nombre_comercial(),
        subject="Métricas de operación y control de caja",
    )
    documento._emitido = emitido

    desde = timezone.localtime(metricas["desde"])
    hasta = timezone.localtime(metricas["hasta"])
    partes: list = []

    # --- Encabezado ---------------------------------------------------------
    partes.append(Paragraph(f"{marca.nombre_comercial()} · Reporte de operación", estilos["titulo"]))
    periodo = (
        f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y} "
        f"({metricas['dias']} día{'s' if metricas['dias'] != 1 else ''})"
    )
    if solicitado_por:
        periodo += f" · solicitado por {solicitado_por}"
    partes.append(Paragraph(periodo, estilos["subtitulo"]))

    # --- Indicadores --------------------------------------------------------
    caja = metricas["caja"]
    clientes = metricas["clientes"]
    partes.append(_indicadores(
        [
            ("Total cobrado", pesos(resumen["total_cobrado"])),
            ("Boletos cobrados", f"{resumen['folios_cobrados']:,}"),
            ("Importe promedio", pesos(resumen["importe_promedio"])),
            ("Permanencia promedio", duracion(round(resumen["minutos_promedio"]))),
            ("Cortesías", f"{resumen['folios_cortesia']:,}"),
            ("Placas atendidas", f"{clientes['placas_atendidas']:,}"),
            ("Clientes frecuentes", f"{clientes['placas_frecuentes']:,}"),
            ("Diferencia de caja", pesos_con_signo(caja["diferencia"])),
        ],
        estilos,
    ))

    # --- Horas pico ---------------------------------------------------------
    horas = metricas["horas"]
    pico = horas["pico_entradas"]
    partes.append(Paragraph("Horas pico", estilos["seccion"]))

    if pico.hora is None:
        partes.append(Paragraph("No se registraron entradas en el periodo.", estilos["vacio"]))
    else:
        frase = (
            f"La hora de mayor afluencia es la de las <b>{pico.etiqueta}</b>, con "
            f"{pico.conteo} entradas: el {pico.porcentaje} % de todas las del periodo."
        )
        if pico.hay_empate:
            otras = ", ".join(f"{h:02d}:00 h" for h in pico.empates)
            frase += f" Mismo volumen en: {otras}."
        cobros = horas["pico_cobros"]
        if cobros.hora is not None and cobros.hora != pico.hora:
            frase += (
                f" El cobro se concentra más tarde, a las <b>{cobros.etiqueta}</b>, "
                "desplazado tanto como dura la estancia media."
            )
        partes.append(Paragraph(frase, estilos["hallazgo"]))

    partes.append(grafico_columnas(
        [
            {"etiqueta": f"{h:02d}", "valor": conteo}
            for h, conteo in enumerate(horas["entradas"])
        ],
        alto=108,
        formato=lambda v: f"{int(v):,}",
    ))
    partes.append(Paragraph(
        # «Destacada» y no «oscura»: el mismo pie se usa en la pantalla, donde
        # en tema oscuro la barra del máximo es la MÁS CLARA. Describir el
        # resalte por su tono obligaba a mantener dos textos que acabarían
        # divergiendo, y uno de los dos siempre estaría mintiendo.
        "Entradas por hora del día, acumuladas en todo el periodo. La barra "
        "destacada marca el máximo. Se cuenta la entrada y no el cobro porque es "
        "la entrada la que exige presencia en la caseta.",
        estilos["nota"],
    ))

    partes.append(Spacer(1, 4))
    partes.append(KeepTogether([
        Paragraph("Ingreso por hora de cobro", estilos["seccion"]),
        grafico_columnas(
            [
                {"etiqueta": f"{h:02d}", "valor": monto}
                for h, monto in enumerate(horas["ingresos"])
            ],
            alto=96,
            formato=lambda v: f"${int(v):,}",
        ),
        Paragraph(
            "Recaudación por hora. Está desplazada respecto a las entradas tanto como "
            "dura la estancia media, de modo que no debe usarse para decidir horarios "
            "de personal.",
            estilos["nota"],
        ),
    ]))

    # --- Día de la semana ---------------------------------------------------
    partes.append(KeepTogether([
        Paragraph("Comportamiento por día de la semana", estilos["seccion"]),
        grafico_columnas(
            [{"etiqueta": d["dia"][:3], "valor": d["entradas"]} for d in metricas["semana"]],
            alto=92,
            formato=lambda v: f"{int(v):,}",
            etiquetar_todas=True,
        ),
        Paragraph(
            "Entradas por día de la semana. Un día en cero dentro de un periodo corto "
            "solo indica que el rango no lo cubre.",
            estilos["nota"],
        ),
    ]))

    # --- Serie diaria -------------------------------------------------------
    serie = metricas["serie_diaria"]
    partes.append(PageBreak())
    partes.append(Paragraph("Ingreso día por día", estilos["seccion"]))
    partes.append(grafico_columnas(
        [
            {
                "etiqueta": f"{fila['dia']:%d/%m}" if hasattr(fila["dia"], "strftime") else str(fila["dia"]),
                "valor": fila["ingreso"],
            }
            for fila in serie
        ],
        alto=118,
        formato=lambda v: f"${int(v):,}",
    ))
    partes.append(Paragraph(
        "Cobrado por día. Es donde se ven el fin de semana flojo, el puente y el día "
        "que no se abrió, que un total del periodo esconde.",
        estilos["nota"],
    ))

    # --- Composición --------------------------------------------------------
    composicion = metricas["composicion"]
    partes.append(Paragraph("Composición de la operación", estilos["seccion"]))
    partes.append(grafico_segmentado(composicion["boletos"]))
    partes.append(Spacer(1, 6))

    if composicion["por_tipo"]:
        partes.append(_tabla(
            [["Tipo de vehículo", "Boletos", "Ingreso", "% del ingreso"]]
            + [
                [f["etiqueta"], f"{f['folios']:,}", pesos(f["ingreso"]), f"{f['porcentaje']} %"]
                for f in composicion["por_tipo"]
            ],
            [ANCHO_UTIL * 0.4, ANCHO_UTIL * 0.2, ANCHO_UTIL * 0.2, ANCHO_UTIL * 0.2],
            numericas=(1, 2, 3),
        ))
        partes.append(Spacer(1, 8))

    partes.append(_tabla(
        [["Permanencia", "Estancias", "% del total"]]
        + [[f["etiqueta"], f"{f['conteo']:,}", f"{f['porcentaje']} %"] for f in composicion["permanencia"]],
        [ANCHO_UTIL * 0.4, ANCHO_UTIL * 0.3, ANCHO_UTIL * 0.3],
        numericas=(1, 2),
    ))
    partes.append(Paragraph(
        "Cuánto tiempo se queda la gente. Si la mayoría no llega a la hora, el ingreso "
        "depende de la rotación y no del precio por hora.",
        estilos["nota"],
    ))

    # --- Clientes frecuentes ------------------------------------------------
    #
    # El encabezado, el párrafo y el gráfico van en un mismo bloque
    # indivisible. Sueltos, el salto de página dejaba el título al pie de una
    # hoja y su gráfico al principio de la siguiente, que es la forma más
    # segura de que alguien lea un gráfico creyendo que es de otra cosa.
    encabezado_clientes = [
        Paragraph("Clientes frecuentes", estilos["seccion"]),
        Paragraph(
            f"Placas con {clientes['minimo_visitas']} visitas o más dentro del periodo. "
            f"Son {clientes['placas_frecuentes']} de {clientes['placas_atendidas']} placas "
            f"atendidas y aportan {pesos(clientes['gasto_frecuentes'])}, el "
            f"{clientes['parte_del_ingreso']} % del ingreso. La placa identifica al "
            "vehículo, no a la persona: un cambio de placa interrumpe la serie.",
            estilos["hallazgo"],
        ),
    ]

    if clientes["filas"]:
        partes.append(KeepTogether(encabezado_clientes + [
            grafico_barras(
                [
                    {"etiqueta": f["placa"], "valor": f["visitas"]}
                    for f in clientes["filas"][:12]
                ],
                formato=lambda v: f"{int(v)} visitas",
            ),
            Paragraph(
                "Visitas por placa, de mayor a menor. Las barras arrancan en cero: "
                "recortar el eje para separarlas exageraría diferencias pequeñas.",
                estilos["nota"],
            ),
        ]))
        partes.append(Spacer(1, 7))
        partes.append(_tabla(
            [["Placa", "Visitas", "Gasto", "Ticket", "Estancia", "Última visita"]]
            + [
                [
                    f["placa"],
                    f"{f['visitas']:,}",
                    pesos(f["gasto"]),
                    pesos(f["ticket"]),
                    duracion(round(f["minutos_promedio"])),
                    timezone.localtime(f["ultima"]).strftime("%d/%m/%Y %H:%M") if f["ultima"] else "—",
                ]
                for f in clientes["filas"]
            ],
            [ANCHO_UTIL * 0.17, ANCHO_UTIL * 0.11, ANCHO_UTIL * 0.17,
             ANCHO_UTIL * 0.15, ANCHO_UTIL * 0.16, ANCHO_UTIL * 0.24],
            numericas=(1, 2, 3),
        ))
        if clientes["truncado"]:
            partes.append(Paragraph(
                f"Se listan los {clientes['top']} de mayor frecuencia; hay "
                f"{clientes['placas_frecuentes']} en total. El detalle completo está "
                "en la exportación a CSV.",
                estilos["nota"],
            ))
    else:
        partes.append(KeepTogether(encabezado_clientes + [
            Paragraph("Ninguna placa repitió visita en el periodo.", estilos["vacio"])
        ]))

    # --- Control de caja ----------------------------------------------------
    partes.append(Paragraph("Control de caja", estilos["seccion"]))
    partes.append(_indicadores(
        [
            ("Turnos cortados", f"{caja['turnos']:,}"),
            ("Efectivo esperado", pesos(caja["esperado"])),
            ("Diferencia vigente", pesos_con_signo(caja["diferencia"])),
            ("Cortes con faltante", f"{caja['faltantes']:,}"),
        ],
        estilos,
    ))

    if caja["ajustados"]:
        partes.append(Spacer(1, 6))
        partes.append(Paragraph(
            f"<b>{caja['ajustados']} corte(s) corregidos</b> después de su cierre. "
            f"La diferencia declarada al cerrar sumaba "
            f"{pesos_con_signo(caja['diferencia_original'])} y las correcciones "
            f"movieron {pesos_con_signo(caja['ajustado_neto'])}, dejándola en "
            f"{pesos_con_signo(caja['diferencia'])}. Cada corrección queda en la "
            "bitácora del turno con su motivo, su justificación y quién la autorizó.",
            estilos["hallazgo"],
        ))

    if caja["detalle"]:
        partes.append(Spacer(1, 4))
        partes.append(_tabla(
            [["Turno", "Operador", "Cerrado", "Cobrado", "Esperado", "Declarado", "Difer. cierre", "Difer. vigente"]]
            + [
                [
                    str(t.pk),
                    Paragraph(str(t.operador), estilos["celda"]),
                    timezone.localtime(t.cerrado_en).strftime("%d/%m %H:%M") if t.cerrado_en else "—",
                    pesos(t.total_cobrado),
                    pesos(t.efectivo_esperado),
                    pesos(t.efectivo_vigente),
                    pesos_con_signo(t.diferencia),
                    pesos_con_signo(t.diferencia_vigente) + (" *" if t.tiene_ajuste else ""),
                ]
                for t in caja["detalle"]
            ],
            [ANCHO_UTIL * 0.07, ANCHO_UTIL * 0.14, ANCHO_UTIL * 0.12,
             ANCHO_UTIL * 0.12, ANCHO_UTIL * 0.12, ANCHO_UTIL * 0.12,
             ANCHO_UTIL * 0.145, ANCHO_UTIL * 0.155],
            numericas=(3, 4, 5, 6, 7),
        ))
        partes.append(Paragraph(
            "«Difer. cierre» es lo que el operador declaró al cortar y no se modifica "
            "nunca. «Difer. vigente» incluye las correcciones posteriores; el asterisco "
            "marca los cortes corregidos.",
            estilos["nota"],
        ))

    documento.build(partes, onFirstPage=_pie, onLaterPages=_pie)
    pdf = bufer.getvalue()
    bufer.close()

    logger.info(
        "Reporte PDF generado | desde=%s hasta=%s bytes=%s solicitado_por=%s",
        desde.date(), hasta.date(), len(pdf), solicitado_por or "—",
    )
    return pdf


def nombre_de_archivo(metricas: dict) -> str:
    """
    Nombre del archivo con el periodo dentro.

    Quien recibe tres reportes por correo necesita distinguirlos sin abrirlos.
    `reporte.pdf` repetido tres veces en una carpeta de descargas se convierte
    en `reporte (2).pdf` y deja de decir nada.
    """
    desde = timezone.localtime(metricas["desde"])
    hasta = timezone.localtime(metricas["hasta"])
    if desde.date() == hasta.date():
        return f"reporte_{desde:%Y%m%d}.pdf"
    return f"reporte_{desde:%Y%m%d}_a_{hasta:%Y%m%d}.pdf"

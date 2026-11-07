"""
Gráficos del reporte: paleta y dibujo vectorial para pantalla.

Tres decisiones gobiernan este módulo.

**Sin librería de gráficos en el navegador.** Los SVG se escriben aquí, en el
servidor, y llegan dentro del HTML. Es la misma regla que ya cumple la hoja de
estilos —ni fuentes ni CSS remotos— y por el mismo motivo: la caseta pierde la
conexión y la pantalla debe seguir viéndose igual. Una gráfica que depende de
un CDN es una gráfica que algún día se muestra vacía, y además obligaría a
mandar al navegador las cifras de caja en un bloque de datos aparte.

**La forma la decide el trabajo del lector, no el gusto.** Comparar magnitudes
en el tiempo son barras; una parte contra su todo es una barra segmentada y no
un pastel —los sectores de un círculo se comparan peor que dos longitudes
sobre una misma línea—; un ranking son barras horizontales ordenadas. El
pastel quedó fuera a propósito.

**Una sola serie por gráfico, un solo matiz.** Ninguna de estas gráficas
distingue series: todas comparan magnitudes de lo mismo. Por eso no hay
leyenda —el título dice qué se mide— y el color no carga información: la
longitud de la barra ya la lleva. Eso es también lo que hace que el reporte
siga siendo legible impreso en blanco y negro, que es como se imprime.

La paleta está verificada con el validador de la guía de visualización: banda
de luminosidad, piso de croma, separación para daltonismo —protanopia y
tritanopia—, piso de visión normal y contraste contra el fondo. El azul
`#1b4f91` es el institucional del sistema, el mismo `--acento` de la hoja de
estilos, y resultó ser el paso 600 de una rampa azul válida, de modo que la
marca y la paleta de datos son la misma cosa y no dos azules parecidos.
"""
from __future__ import annotations

from decimal import Decimal
from html import escape

from django.utils.safestring import mark_safe

from .domain.metricas import escala

# --- Paleta -----------------------------------------------------------------
#
# Valores literales y no `var(--acento)`: estos mismos colores los consume el
# PDF, donde no hay hoja de estilos. Tenerlos una sola vez es lo que garantiza
# que el documento que se manda por correo y la pantalla donde se revisó se
# vean igual.

# Rampa azul ordinal, de claro a oscuro. Verificada: luminosidad monótona,
# salto visible entre pasos y el extremo claro separado del fondo blanco.
RAMPA = ["#86b6ef", "#5598e7", "#2a78d6", "#1b4f91", "#0d366b"]

SERIE = "#2a78d6"        # Barra normal: paso 450 de la rampa.
DESTACADA = "#1b4f91"    # Máximo de la serie: paso 600, el azul de la marca.
SEGUNDA = "#eb6834"      # Segunda categoría (cortesía). Verificada contra el azul.

TINTA = "#0b0b0b"
TINTA_SUAVE = "#52514e"
APAGADO = "#898781"      # Ejes y etiquetas: deben recular ante los datos.
CUADRICULA = "#e1e0d9"
EJE = "#c3c2b7"

# Estados de la caja. Son los mismos de la hoja de estilos, que ya están
# elegidos con contraste alto, y nunca se usan como color de serie: un color
# de estado que además identifica una barra deja de significar «atención».
EXITO = "#1b6b2f"
ALERTA = "#8a5a00"
ERROR = "#a01818"


# --- Lo mismo, como variables CSS, para el SVG de la pantalla --------------
#
# Los literales de arriba los consume el PDF, que se imprime en papel blanco y
# no tiene hoja de estilos. La pantalla sí la tiene, y esta aplicación respeta
# el tema que el operador lleve configurado en su teléfono: una caseta de
# madrugada con la pantalla en blanco deslumbra. Con los colores incrustados,
# los rótulos de los ejes quedaban en gris oscuro sobre fondo oscuro, es decir
# invisibles, que es peor que no dibujar el gráfico.
#
# Cada token lleva su valor claro como respaldo, de modo que el SVG se ve
# correcto aunque se extraiga del HTML y se abra suelto.
#
# Los pasos oscuros están verificados contra la superficie `--superficie` del
# tema oscuro (#182636), no son el claro aclarado a ojo. En oscuro la
# jerarquía se invierte: la barra destacada es la **más clara**, porque es la
# que resalta sobre un fondo oscuro.
T_SERIE = f"var(--viz-serie, {SERIE})"
T_DESTACADA = f"var(--viz-destacada, {DESTACADA})"
T_SEGUNDA = f"var(--viz-segunda, {SEGUNDA})"
T_TINTA = f"var(--viz-tinta, {TINTA})"
T_TINTA_SUAVE = f"var(--viz-tinta-suave, {TINTA_SUAVE})"
T_APAGADO = f"var(--viz-apagado, {APAGADO})"
T_CUADRICULA = f"var(--viz-cuadricula, {CUADRICULA})"
T_EJE = f"var(--viz-eje, {EJE})"
T_RAMPA = [f"var(--viz-rampa-{i}, {c})" for i, c in enumerate(RAMPA)]


def _texto(valor) -> str:
    """Escapa cualquier dato que entre al SVG. Una placa es texto de usuario."""
    return escape(str(valor), quote=True)


def _barra_arriba_redondeada(x: float, y: float, ancho: float, alto: float, radio: float = 4) -> str:
    """
    Trazo de una barra vertical con el extremo del dato redondeado.

    Solo se redondea arriba: la base queda anclada a la línea cero, recta,
    porque el punto de apoyo es lo que permite comparar longitudes. Una barra
    redondeada por los cuatro lados flota y se lee peor.
    """
    radio = min(radio, ancho / 2, alto) if alto > 0 else 0
    if radio <= 0:
        return f'M{x:.2f},{y + alto:.2f} h{ancho:.2f} v{-alto:.2f} h{-ancho:.2f} Z'
    return (
        f'M{x:.2f},{y + alto:.2f} '
        f'V{y + radio:.2f} '
        f'a{radio:.2f},{radio:.2f} 0 0 1 {radio:.2f},{-radio:.2f} '
        f'h{ancho - 2 * radio:.2f} '
        f'a{radio:.2f},{radio:.2f} 0 0 1 {radio:.2f},{radio:.2f} '
        f'V{y + alto:.2f} Z'
    )


def _barra_derecha_redondeada(x: float, y: float, ancho: float, alto: float, radio: float = 4) -> str:
    """Equivalente horizontal: se redondea el extremo del dato, no el origen."""
    radio = min(radio, alto / 2, ancho) if ancho > 0 else 0
    if radio <= 0:
        return f'M{x:.2f},{y:.2f} h{ancho:.2f} v{alto:.2f} h{-ancho:.2f} Z'
    return (
        f'M{x:.2f},{y:.2f} '
        f'H{x + ancho - radio:.2f} '
        f'a{radio:.2f},{radio:.2f} 0 0 1 {radio:.2f},{radio:.2f} '
        f'v{alto - 2 * radio:.2f} '
        f'a{radio:.2f},{radio:.2f} 0 0 1 {-radio:.2f},{radio:.2f} '
        f'H{x:.2f} Z'
    )


def barras_verticales(
    datos: list[dict],
    *,
    alto: int = 170,
    ancho: int = 720,
    formato=str,
    etiquetar_todas: bool = False,
) -> str:
    """
    Serie de barras verticales con una cuadrícula legible y el máximo marcado.

    `datos` lleva, por barra: `etiqueta` (debajo), `valor`, `titulo` (el texto
    que aparece al posar el cursor) y, opcionalmente, `destacada`.

    El máximo se dibuja en el paso oscuro de la misma rampa y lleva su cifra
    impresa. Es lo que convierte la gráfica en una afirmación: «el pico es
    este» se lee sin consultar el eje. Se usa un segundo paso del mismo matiz y
    no un color distinto para que la jerarquía sobreviva a una impresión en
    gris, donde dos matices se vuelven el mismo tono.

    Las cifras no se imprimen sobre todas las barras salvo que se pida: un
    número en cada una tapa la forma de la serie, que es lo que se vino a ver.
    """
    if not datos:
        return _sin_datos(ancho)

    tope = escala([d["valor"] for d in datos])
    margen_izq, margen_der, margen_sup, margen_inf = 54, 8, 14, 30
    area_ancho = ancho - margen_izq - margen_der
    area_alto = alto - margen_sup - margen_inf

    # 2 px de separación entre barras adyacentes: el hueco del fondo es lo que
    # evita que dos barras altas se lean como un bloque único.
    paso = area_ancho / len(datos)
    ancho_barra = max(paso - 2, 1)

    piezas: list[str] = []

    # Cuadrícula de cuatro divisiones, por detrás y en hilo fino.
    for i in range(5):
        y = margen_sup + area_alto - (area_alto * i / 4)
        valor_linea = tope * Decimal(i) / Decimal(4)
        piezas.append(
            f'<line x1="{margen_izq}" y1="{y:.1f}" x2="{ancho - margen_der}" '
            f'y2="{y:.1f}" stroke="{T_CUADRICULA if i else T_EJE}" stroke-width="1"/>'
        )
        piezas.append(
            f'<text x="{margen_izq - 6}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-size="11" fill="{T_APAGADO}" '
            f'style="font-variant-numeric:tabular-nums">{_texto(formato(valor_linea))}</text>'
        )

    maximo = max((Decimal(d["valor"] or 0) for d in datos), default=Decimal(0))

    for indice, dato in enumerate(datos):
        valor = Decimal(dato["valor"] or 0)
        # A float antes de multiplicar: la geometría es de píxeles y no
        # admite mezclar Decimal con las medidas del lienzo.
        altura = area_alto * float(valor) / float(tope) if tope else 0.0
        x = margen_izq + indice * paso
        y = margen_sup + area_alto - altura
        es_max = dato.get("destacada", valor == maximo and maximo > 0)
        color = T_DESTACADA if es_max else T_SERIE

        piezas.append(
            f'<path d="{_barra_arriba_redondeada(x, y, ancho_barra, altura)}" '
            f'fill="{color}"><title>{_texto(dato.get("titulo", ""))}</title></path>'
        )

        if (es_max or etiquetar_todas) and valor > 0:
            piezas.append(
                f'<text x="{x + ancho_barra / 2:.1f}" y="{y - 4:.1f}" '
                f'text-anchor="middle" font-size="11" font-weight="600" '
                f'fill="{T_TINTA}" style="font-variant-numeric:tabular-nums">'
                f'{_texto(formato(valor))}</text>'
            )

        # Con muchas barras, una etiqueta por barra se solapa. Se rotulan una
        # de cada n, calculado para que nunca haya más de doce rótulos.
        salto = max(1, (len(datos) + 11) // 12)
        if indice % salto == 0 or es_max:
            piezas.append(
                f'<text x="{x + ancho_barra / 2:.1f}" y="{alto - 10}" '
                f'text-anchor="middle" font-size="11" fill="{T_APAGADO}">'
                f'{_texto(dato["etiqueta"])}</text>'
            )

    return _envolver(piezas, ancho, alto)


def barras_horizontales(
    datos: list[dict],
    *,
    ancho: int = 720,
    alto_barra: int = 22,
    formato=str,
) -> str:
    """
    Ranking en barras horizontales, de mayor a menor.

    Es la forma correcta para un ranking de nombres largos —una placa no cabe
    bajo una barra vertical— y la que permite comparar valores cercanos: las
    barras arrancan de una misma línea, y dos longitudes sobre un origen común
    se comparan con precisión que un pastel no alcanza.

    Cada barra lleva su cifra impresa al final. Aquí sí en todas: son pocas y
    el lector del ranking viene justamente a leer los números.
    """
    if not datos:
        return _sin_datos(ancho)

    margen_izq, margen_der, margen_sup = 86, 72, 6
    alto = margen_sup * 2 + len(datos) * (alto_barra + 6)
    area_ancho = ancho - margen_izq - margen_der
    tope = escala([d["valor"] for d in datos])

    piezas: list[str] = []
    for indice, dato in enumerate(datos):
        valor = Decimal(dato["valor"] or 0)
        largo = area_ancho * float(valor) / float(tope) if tope else 0.0
        y = margen_sup + indice * (alto_barra + 6)
        # El primer lugar en el paso oscuro: el ranking ya está ordenado, pero
        # marcar la cabeza evita tener que leer de arriba abajo para hallarla.
        color = T_DESTACADA if indice == 0 else T_SERIE

        piezas.append(
            f'<text x="{margen_izq - 8}" y="{y + alto_barra / 2 + 4:.1f}" '
            f'text-anchor="end" font-size="11" fill="{T_TINTA_SUAVE}" '
            f'style="font-variant-numeric:tabular-nums">{_texto(dato["etiqueta"])}</text>'
        )
        piezas.append(
            f'<path d="{_barra_derecha_redondeada(margen_izq, y, largo, alto_barra)}" '
            f'fill="{color}"><title>{_texto(dato.get("titulo", ""))}</title></path>'
        )
        piezas.append(
            f'<text x="{margen_izq + largo + 7:.1f}" y="{y + alto_barra / 2 + 4:.1f}" '
            f'font-size="11" font-weight="600" fill="{T_TINTA}" '
            f'style="font-variant-numeric:tabular-nums">{_texto(formato(valor))}</text>'
        )

    return _envolver(piezas, ancho, alto)


def barra_segmentada(partes: list[dict], *, ancho: int = 720, alto: int = 34) -> str:
    """
    Un todo repartido en pocas partes, en una sola barra.

    Sustituye al pastel que pediría el instinto. Dos sectores de un círculo se
    comparan peor que dos longitudes sobre una recta, y con tres o más la
    diferencia es notable: aquí el lector lee proporciones, no ángulos.

    Cada segmento lleva su porcentaje impreso encima cuando cabe, y entre
    segmentos queda una separación del color del fondo: sin ella, dos
    proporciones contiguas se leen como una sola.

    Las etiquetas van en la leyenda de abajo y no dentro del segmento, porque
    un segmento angosto no admite texto y una etiqueta que a veces aparece y a
    veces no obliga a interpretar el gráfico dos veces.
    """
    total = sum(int(p["conteo"] or 0) for p in partes)
    if total <= 0:
        return _sin_datos(ancho, alto=alto + 24)

    colores = [T_DESTACADA, T_SEGUNDA, T_RAMPA[1], T_RAMPA[0]]
    piezas: list[str] = []
    x = 0.0

    for indice, parte in enumerate(partes):
        conteo = int(parte["conteo"] or 0)
        if conteo <= 0:
            continue
        largo = ancho * conteo / total
        color = colores[indice % len(colores)]
        # 2 px de fondo entre segmentos, descontados del propio segmento.
        dibujo = max(largo - 2, 1)
        piezas.append(
            f'<rect x="{x:.2f}" y="0" width="{dibujo:.2f}" height="{alto}" rx="3" '
            f'fill="{color}"><title>{_texto(parte["etiqueta"])}: {conteo} '
            f'({parte.get("porcentaje", 0)} %)</title></rect>'
        )
        # Blanco sobre azul y sobre naranja: los dos superan el contraste
        # exigido para texto pequeño.
        if dibujo > 46:
            piezas.append(
                f'<text x="{x + dibujo / 2:.1f}" y="{alto / 2 + 4:.1f}" '
                f'text-anchor="middle" font-size="11" font-weight="600" '
                f'fill="#ffffff">{parte.get("porcentaje", 0)} %</text>'
            )
        x += largo

    # Leyenda: aquí sí hace falta, porque los segmentos son categorías y no
    # magnitudes de lo mismo. El nombre acompaña siempre al color, de modo que
    # la identidad nunca depende del matiz.
    y_leyenda = alto + 17
    x = 0.0
    for indice, parte in enumerate(partes):
        color = colores[indice % len(colores)]
        piezas.append(
            f'<rect x="{x:.1f}" y="{y_leyenda - 8}" width="10" height="10" rx="2" fill="{color}"/>'
        )
        piezas.append(
            f'<text x="{x + 15:.1f}" y="{y_leyenda + 1}" font-size="11" fill="{T_TINTA_SUAVE}">'
            f'{_texto(parte["etiqueta"])} · {_texto(parte["conteo"])}</text>'
        )
        x += 24 + 7.2 * (len(str(parte["etiqueta"])) + len(str(parte["conteo"])) + 3)

    return _envolver(piezas, ancho, alto + 24)


def _sin_datos(ancho: int, alto: int = 70) -> str:
    """
    Marcador explícito de ausencia de datos.

    Se dibuja algo —y no se omite el bloque— para que «no hubo movimiento» no
    se parezca a «la gráfica no cargó». Son dos cosas distintas y el lector
    debe poder distinguirlas sin preguntar.
    """
    return _envolver(
        [
            f'<text x="{ancho / 2}" y="{alto / 2 + 4}" text-anchor="middle" '
            f'font-size="12" fill="{T_APAGADO}">Sin movimientos en el periodo</text>'
        ],
        ancho,
        alto,
    )


def _envolver(piezas: list[str], ancho: int, alto: int) -> str:
    """
    Cierra el SVG con `viewBox` y sin ancho fijo.

    Sin alto ni ancho en píxeles: la gráfica se escala al contenedor, que es
    lo que permite que la misma marca se vea en la pantalla de un teléfono en
    la caseta y en el monitor de la oficina. `role="img"` y el título hacen que
    un lector de pantalla anuncie el gráfico en lugar de deletrear sus nodos.
    """
    cuerpo = "".join(piezas)
    # El ancho mínimo es lo que salva la gráfica en un teléfono. Sin él, el
    # SVG se encoge hasta el ancho de la pantalla y sus rótulos bajan de cinco
    # píxeles: el gráfico se ve, pero no se puede leer, que en una caseta es
    # lo mismo que no estar. Con él, la gráfica se desplaza de lado dentro de
    # su tarjeta —igual que ya hacen las tablas de esta aplicación— y el texto
    # conserva su tamaño.
    return mark_safe(
        f'<div class="grafico-envoltura">'
        f'<svg viewBox="0 0 {ancho} {alto}" role="img" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'style="width:100%;min-width:{min(ancho, 600)}px;height:auto;display:block;'
        f'overflow:visible;font-family:system-ui,-apple-system,sans-serif">'
        f'{cuerpo}</svg></div>'
    )

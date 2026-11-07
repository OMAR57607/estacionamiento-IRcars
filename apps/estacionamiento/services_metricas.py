"""
Capa de aplicación: métricas de operación del periodo.

Vive aparte de `services_caja.py` porque responde a otra pregunta. El control
de caja pregunta «¿cuadra el efectivo?» y se juega en un turno; esto pregunta
«¿cómo se comporta el negocio?» y se juega en semanas. Mezclarlos haría que
una consulta de reporte compartiera módulo con el cierre ciego, que es el
código más delicado del sistema.

Dos reglas gobiernan todo lo que hay aquí:

* **Se agrega en la base, no en memoria.** Cada métrica es una consulta con
  `GROUP BY`. Traer los folios de un mes para contarlos en Python funciona en
  las pruebas y se cae el día que el periodo es un año.
* **La aritmética y las conclusiones viven en `domain/metricas.py`.** Este
  módulo consulta y ordena; decidir cuál es la hora pico o en qué tramo cae
  una estancia es lógica pura y se prueba sin base de datos.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, F, Max, Q, Sum
from django.db.models.functions import (
    Coalesce,
    ExtractHour,
    ExtractIsoWeekDay,
    TruncDate,
)
from django.utils import timezone

from .domain.exceptions import ErrorConfiguracion
from .domain.metricas import (
    DIAS_SEMANA,
    Pico,
    distribuir_permanencia,
    hora_pico,
    porcentaje,
    ticket_promedio,
)
from .models import EstadoFolio, EstadoTurno, Folio, TipoVehiculo, Turno

logger = logging.getLogger(__name__)

# Cuántos clientes frecuentes lista el reporte. Veinte caben en una página y
# alcanzan para decidir: más allá, la cola larga de placas que vinieron dos
# veces no cambia ninguna decisión y sí vuelve el documento ilegible.
TOP_CLIENTES = 20

# Desde cuántas visitas una placa deja de ser un cliente de paso. Dos es el
# mínimo que significa algo: una sola visita no distingue al vecino que vuelve
# a diario del que pasaba por ahí una vez en su vida.
MINIMO_VISITAS = 2

# Tipo monetario explícito para `Coalesce`: sin él, mezclar un campo decimal
# con el literal cero deja que el motor decida el tipo del resultado, y
# PostgreSQL y SQLite no deciden lo mismo.
DINERO = DecimalField(max_digits=14, decimal_places=2)
CERO = Decimal("0.00")


def _cobrados(desde: datetime, hasta: datetime):
    """
    Folios cobrados en el periodo, base de casi todas las métricas.

    Se filtra por `salida_en` y no por `entrada_en`: el ingreso pertenece al
    momento en que se cobró. Un vehículo que entró el 31 y salió el 1 aporta
    su importe al mes nuevo, que es como lo cuenta la caja.
    """
    return Folio.objects.filter(
        estado=EstadoFolio.CERRADO, salida_en__gte=desde, salida_en__lte=hasta
    )


# --- Horas pico --------------------------------------------------------------


def _vector_de_horas(consulta, campo: str, valor) -> list:
    """
    Convierte un `GROUP BY hora` en un vector de 24 posiciones.

    La consulta solo devuelve las horas con movimiento. El vector completo
    —con sus ceros— es lo que permite que el gráfico tenga siempre el mismo
    ancho y que `hora_pico` no confunda el índice de la lista con la hora del
    reloj.
    """
    agrupado = (
        consulta.annotate(hora=ExtractHour(campo))
        .values("hora")
        .annotate(dato=valor)
        .order_by("hora")
    )
    vector = [0] * 24
    for fila in agrupado:
        if fila["hora"] is not None:
            vector[int(fila["hora"])] = fila["dato"] or 0
    return vector


def horas_del_dia(desde: datetime, hasta: datetime) -> dict:
    """
    Perfil horario del periodo: a qué hora llega la gente y a qué hora se cobra.

    Se devuelven **dos** curvas porque responden a preguntas distintas y
    confundirlas lleva a decisiones equivocadas:

    * **Entradas por hora** mide la demanda. Es la que dice a qué hora hace
      falta alguien en la caseta, y se cuenta sobre `entrada_en`.
    * **Ingreso por hora** mide la recaudación, y se cuenta sobre `salida_en`.
      Está desplazada respecto a la anterior tanto como dure la estancia
      media: en un estacionamiento de oficinas, el pico de entradas es a las
      8:00 y el de cobro a las 18:00.

    El pico se informa sobre las entradas. Contratar personal con la curva de
    cobro pondría a la gente a trabajar cuando la fila ya se formó.

    Las entradas se cuentan sobre todos los folios del periodo, cobrados o no,
    porque un vehículo que entró ocupó un lugar y necesitó atención aunque su
    folio se cancelara o siga abierto.
    """
    entradas_consulta = Folio.objects.filter(
        entrada_en__gte=desde, entrada_en__lte=hasta
    ).exclude(estado=EstadoFolio.CANCELADO)

    entradas = _vector_de_horas(entradas_consulta, "entrada_en", Count("id"))
    ingresos = _vector_de_horas(
        _cobrados(desde, hasta),
        "salida_en",
        Coalesce(Sum("importe"), CERO, output_field=DINERO),
    )
    salidas = _vector_de_horas(_cobrados(desde, hasta), "salida_en", Count("id"))

    return {
        "entradas": entradas,
        "salidas": salidas,
        "ingresos": ingresos,
        "pico_entradas": hora_pico(entradas),
        # El pico de recaudación se calcula sobre el conteo de cobros y no
        # sobre el importe: una hora con un solo vehículo de estancia larga
        # recauda mucho sin ser una hora ocupada, y «hora pico» se usa para
        # decidir presencia de personal.
        "pico_cobros": hora_pico(salidas),
    }


def dias_de_semana(desde: datetime, hasta: datetime) -> list[dict]:
    """
    Entradas e ingreso por día de la semana.

    Devuelve los siete días aunque el periodo no los cubra: un rango de tres
    días con cuatro días en blanco es información —dice que el reporte es
    corto— mientras que un gráfico de tres barras sin aviso se lee como si la
    semana tuviera tres días.
    """
    agrupado = (
        Folio.objects.filter(entrada_en__gte=desde, entrada_en__lte=hasta)
        .exclude(estado=EstadoFolio.CANCELADO)
        .annotate(dia=ExtractIsoWeekDay("entrada_en"))
        .values("dia")
        .annotate(
            entradas=Count("id"),
            ingreso=Coalesce(
                Sum("importe", filter=Q(estado=EstadoFolio.CERRADO)),
                CERO,
                output_field=DINERO,
            ),
        )
    )
    # `ExtractIsoWeekDay` numera 1 = lunes … 7 = domingo, el mismo orden que
    # `DIAS_SEMANA`. Depender de esa coincidencia sin decirlo sería frágil.
    por_dia = {int(f["dia"]): f for f in agrupado if f["dia"] is not None}

    total = sum(f["entradas"] for f in por_dia.values())
    return [
        {
            "dia": nombre,
            "entradas": por_dia.get(indice + 1, {}).get("entradas", 0),
            "ingreso": por_dia.get(indice + 1, {}).get("ingreso", CERO),
            "porcentaje": porcentaje(
                por_dia.get(indice + 1, {}).get("entradas", 0), total
            ),
        }
        for indice, nombre in enumerate(DIAS_SEMANA)
    ]


def dias_del_periodo(desde: datetime, hasta: datetime) -> list[dict]:
    """
    Serie diaria de ingreso y volumen, en orden cronológico.

    Es la métrica que muestra la tendencia: un gráfico de barras por día
    revela el fin de semana flojo, el puente y el día que alguien no abrió,
    cosas que un total mensual esconde por completo.
    """
    agrupado = (
        _cobrados(desde, hasta)
        .annotate(dia=TruncDate("salida_en"))
        .values("dia")
        .annotate(
            folios=Count("id"),
            ingreso=Coalesce(Sum("importe"), CERO, output_field=DINERO),
            cortesias=Count("id", filter=Q(importe=CERO)),
        )
        .order_by("dia")
    )
    return [
        {
            "dia": fila["dia"],
            "folios": fila["folios"],
            "ingreso": fila["ingreso"],
            "cortesias": fila["cortesias"],
        }
        for fila in agrupado
        if fila["dia"] is not None
    ]


# --- Clientes ----------------------------------------------------------------


def clientes_frecuentes(
    desde: datetime,
    hasta: datetime,
    *,
    top: int = TOP_CLIENTES,
    minimo_visitas: int = MINIMO_VISITAS,
) -> dict:
    """
    Placas que volvieron, ordenadas por número de visitas.

    «Frecuente» se define dentro del periodo consultado y desde
    `minimo_visitas` visitas. Que el umbral sea un parámetro y no una
    constante incrustada importa: en un reporte de un día, dos visitas de la
    misma placa es un dato notable; en uno de un año, es ruido.

    Se agrupa por placa porque es el único identificador del cliente que este
    sistema conoce. Tiene un límite que conviene no olvidar al leer el
    reporte: la placa identifica al vehículo, no a la persona, y un cambio de
    placa rompe la serie histórica de ese cliente. Para lo que sirve —saber si
    conviene ofrecer una pensión y a quién— es suficiente.

    Se devuelven también los totales del grupo: sin ellos, el reporte dice
    quiénes son los frecuentes pero no cuánto del ingreso dependen de ellos,
    que es la cifra con la que se decide si vale la pena retenerlos.
    """
    if top < 1:
        raise ErrorConfiguracion("El tamaño de la lista debe ser al menos 1.")
    if minimo_visitas < 2:
        raise ErrorConfiguracion(
            "Un cliente frecuente requiere al menos dos visitas: con una no "
            "hay nada que distinga al que vuelve del que pasaba por ahí."
        )

    cobrados = _cobrados(desde, hasta)

    agrupado = (
        cobrados.values("placa")
        .annotate(
            visitas=Count("id"),
            gasto=Coalesce(Sum("importe"), CERO, output_field=DINERO),
            minutos=Coalesce(Avg("minutos"), 0.0),
            ultima=Max("salida_en"),
        )
        .filter(visitas__gte=minimo_visitas)
        # Desempate por gasto y luego por placa: sin un orden total, dos
        # clientes con las mismas visitas pueden alternar de posición entre
        # dos ejecuciones del mismo reporte, y quien lo imprime dos veces
        # concluye que el sistema miente.
        .order_by("-visitas", "-gasto", "placa")
    )

    # Las filas se materializan una sola vez y los totales del grupo se suman
    # en Python. Parece ir contra la regla de agregar en la base, y es
    # deliberado: `Sum("gasto")` sobre esta consulta pide la suma de una
    # columna que ya es un agregado, y Django la envuelve en una subconsulta
    # que devuelve cero en silencio. Un total en cero junto a filas con cifras
    # correctas es el peor error posible en un reporte —no se ve roto, se ve
    # falso— y no vale ahorrarse un recorrido a ese precio. La lista está
    # acotada por las placas que repitieron visita, que son una fracción de
    # las atendidas.
    filas_todas = list(agrupado)

    gasto_periodo = cobrados.aggregate(
        total=Coalesce(Sum("importe"), CERO, output_field=DINERO)
    )["total"]
    gasto_grupo = sum((f["gasto"] or CERO for f in filas_todas), CERO)
    visitas_grupo = sum(f["visitas"] for f in filas_todas)

    filas = [
        {
            "placa": fila["placa"],
            "visitas": fila["visitas"],
            "gasto": fila["gasto"],
            "ticket": ticket_promedio(fila["gasto"], fila["visitas"]),
            "minutos_promedio": round(float(fila["minutos"] or 0), 1),
            "ultima": fila["ultima"],
            "parte_del_ingreso": porcentaje(fila["gasto"], gasto_periodo),
        }
        for fila in filas_todas[:top]
    ]

    return {
        "minimo_visitas": minimo_visitas,
        "top": top,
        "filas": filas,
        "placas_frecuentes": len(filas_todas),
        "visitas_frecuentes": visitas_grupo,
        "gasto_frecuentes": gasto_grupo,
        "parte_del_ingreso": porcentaje(gasto_grupo, gasto_periodo),
        # Placas distintas atendidas en el periodo, frecuentes o no. Es el
        # denominador que convierte «40 clientes frecuentes» en una tasa de
        # retención interpretable.
        "placas_atendidas": cobrados.values("placa").distinct().count(),
        "truncado": len(filas_todas) > top,
    }


# --- Composición de la operación --------------------------------------------


def composicion(desde: datetime, hasta: datetime) -> dict:
    """
    De qué está hecho el ingreso: tipo de vehículo, cortesías y quién cobró.

    Las cortesías se separan del ingreso porque no son una categoría de venta
    sino una excepción autorizada, y su volumen es un indicador de control:
    un periodo en que el 30 % de los boletos sale sin cobrar merece una
    pregunta, y esa pregunta no se formula si la cifra está sumada dentro del
    total de boletos.
    """
    cobrados = _cobrados(desde, hasta)

    por_tipo = []
    agrupado_tipo = (
        cobrados.values("tarifa__tipo_vehiculo")
        .annotate(
            folios=Count("id"),
            ingreso=Coalesce(Sum("importe"), CERO, output_field=DINERO),
        )
        .order_by("-ingreso")
    )
    etiquetas = dict(TipoVehiculo.choices)
    total_ingreso = sum((f["ingreso"] for f in agrupado_tipo), CERO)
    for fila in agrupado_tipo:
        clave = fila["tarifa__tipo_vehiculo"]
        por_tipo.append(
            {
                "tipo": clave,
                "etiqueta": etiquetas.get(clave, clave or "Sin tipo"),
                "folios": fila["folios"],
                "ingreso": fila["ingreso"],
                "porcentaje": porcentaje(fila["ingreso"], total_ingreso),
            }
        )

    por_operador = [
        {
            "operador": fila["operador_salida__username"] or "Sin identificar",
            "folios": fila["folios"],
            "ingreso": fila["ingreso"],
            "cortesias": fila["cortesias"],
        }
        for fila in cobrados.values("operador_salida__username")
        .annotate(
            folios=Count("id"),
            ingreso=Coalesce(Sum("importe"), CERO, output_field=DINERO),
            cortesias=Count("id", filter=Q(importe=CERO)),
        )
        .order_by("-ingreso")
    ]

    agregados = cobrados.aggregate(
        folios=Count("id"), cortesias=Count("id", filter=Q(importe=CERO))
    )
    folios = agregados["folios"] or 0
    cortesias = agregados["cortesias"] or 0

    return {
        "por_tipo": por_tipo,
        "por_operador": por_operador,
        # Dos partes de un mismo todo. Se entregan ya calculadas para que la
        # plantilla y el PDF no repitan la resta y acaben discrepando.
        "boletos": [
            {
                "etiqueta": "Con cobro",
                "conteo": folios - cortesias,
                "porcentaje": porcentaje(folios - cortesias, folios),
            },
            {
                "etiqueta": "Cortesía",
                "conteo": cortesias,
                "porcentaje": porcentaje(cortesias, folios),
            },
        ],
        "permanencia": distribuir_permanencia(
            cobrados.values_list("minutos", flat=True).iterator()
        ),
    }


# --- Control de caja del periodo --------------------------------------------


def cortes_del_periodo(desde: datetime, hasta: datetime) -> dict:
    """
    Los cortes cerrados en el periodo y su diferencia **vigente**.

    Es la métrica que une el control de caja con el reporte del dueño: cuánto
    se cobró según los folios, cuánto efectivo se declaró y cuánto falta o
    sobra en conjunto.

    La diferencia que se suma es la vigente —la corregida, si el corte se
    ajustó— y no la del cierre. Sumar las originales declararía en el reporte
    del mes un faltante que ya se demostró inexistente. Las dos cifras se
    devuelven por separado para que el documento pueda decir cuánto de la
    diferencia original era un error de conteo: un periodo donde casi toda la
    diferencia se ajustó después apunta a un problema de procedimiento en el
    cierre, no a una fuga de efectivo.
    """
    turnos = Turno.objects.select_related("operador").filter(
        estado=EstadoTurno.CERRADO, cerrado_en__gte=desde, cerrado_en__lte=hasta
    )

    # El alias NO puede llamarse `diferencia` ni `diferencia_vigente`: lo
    # primero haría que `Sum("diferencia")` sumara el propio agregado en lugar
    # del campo —Django resuelve el alias antes que la columna y falla con
    # «'diferencia' is an aggregate»— y lo segundo taparía la propiedad del
    # modelo que lee la cifra corregida.
    dif_vigente = Coalesce(F("diferencia_ajustada"), F("diferencia"), output_field=DINERO)

    agregados = turnos.annotate(dif_vigente=dif_vigente).aggregate(
        turnos=Count("id"),
        cobrado=Coalesce(Sum("total_cobrado"), CERO, output_field=DINERO),
        esperado=Coalesce(Sum("efectivo_esperado"), CERO, output_field=DINERO),
        suma_vigente=Coalesce(Sum("dif_vigente"), CERO, output_field=DINERO),
        suma_cierre=Coalesce(Sum("diferencia"), CERO, output_field=DINERO),
        faltantes=Count("id", filter=Q(dif_vigente__lt=0)),
        sobrantes=Count("id", filter=Q(dif_vigente__gt=0)),
        ajustados=Count("id", filter=Q(efectivo_ajustado__isnull=False)),
    )

    vigente = agregados["suma_vigente"] or CERO
    cierre = agregados["suma_cierre"] or CERO

    return {
        "turnos": agregados["turnos"],
        "cobrado": agregados["cobrado"],
        "esperado": agregados["esperado"],
        "diferencia": vigente,
        "diferencia_original": cierre,
        "faltantes": agregados["faltantes"],
        "sobrantes": agregados["sobrantes"],
        "ajustados": agregados["ajustados"],
        # Lo que las correcciones movieron en total. Es la cifra que un
        # auditor pedirá primero.
        "ajustado_neto": vigente - cierre,
        # Sin anotación: los turnos llegan como instancias y la cifra vigente
        # se lee de `Turno.diferencia_vigente`, que ya aplica la regla.
        "detalle": list(turnos.order_by("-cerrado_en")[:100]),
    }


# --- Fachada -----------------------------------------------------------------


def metricas_por_rango(
    desde: datetime,
    hasta: datetime,
    *,
    top_clientes: int = TOP_CLIENTES,
    minimo_visitas: int = MINIMO_VISITAS,
) -> dict:
    """
    Todas las métricas del periodo en una sola llamada.

    Es el único punto de entrada que usan la pantalla de reportes y el PDF, y
    eso es deliberado: si cada uno armara su propio conjunto de consultas, el
    documento que se manda por correo y la pantalla donde se revisó antes de
    mandarlo acabarían mostrando cifras distintas del mismo periodo.
    """
    if hasta < desde:
        raise ErrorConfiguracion("El rango de fechas está invertido.")

    return {
        "desde": desde,
        "hasta": hasta,
        "generado_en": timezone.now(),
        "dias": (timezone.localtime(hasta).date() - timezone.localtime(desde).date()).days + 1,
        "horas": horas_del_dia(desde, hasta),
        "semana": dias_de_semana(desde, hasta),
        "serie_diaria": dias_del_periodo(desde, hasta),
        "clientes": clientes_frecuentes(
            desde, hasta, top=top_clientes, minimo_visitas=minimo_visitas
        ),
        "composicion": composicion(desde, hasta),
        "caja": cortes_del_periodo(desde, hasta),
    }

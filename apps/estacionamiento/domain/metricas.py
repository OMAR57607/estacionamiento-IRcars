"""
Lógica pura de las métricas de operación.

Mismo criterio que el motor de tarifas y el de corte: aquí no entra Django, ni
el ORM, ni la plantilla. Lo que vive en este módulo son las decisiones que
convierten un montón de conteos en una afirmación —«la hora pico es la de las
13:00», «el 62 % de las estancias no pasa de una hora»— y esas decisiones
tienen consecuencias: con ellas se contrata personal y se fija un horario.

Separarlas de la consulta permite probarlas con listas escritas a mano, sin
base de datos, y con los casos que la realidad produce y nadie recuerda
probar contra una base: el día sin un solo movimiento, el empate entre dos
horas, el periodo de un único folio.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENTAVO = Decimal("0.01")

# Nombres en español y en orden de semana mexicana: el reporte lo lee el dueño
# del negocio, no un sistema. `date.weekday()` devuelve 0 para lunes.
DIAS_SEMANA = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
]

# Cortes de la distribución de permanencia, en minutos, y su etiqueta.
#
# No son tramos uniformes a propósito. Lo que decide el negocio está en la
# parte baja —si la mayoría de las estancias no llega a la hora, el ingreso
# depende de la rotación y no del precio por hora— y ahí los tramos son
# estrechos. Arriba se agrupa: entre siete y nueve horas no hay ninguna
# decisión distinta que tomar.
TRAMOS_PERMANENCIA: list[tuple[int | None, str]] = [
    (30, "Hasta 30 min"),
    (60, "31 a 60 min"),
    (120, "1 a 2 h"),
    (240, "2 a 4 h"),
    (480, "4 a 8 h"),
    (None, "Más de 8 h"),
]


def _redondear(monto: Decimal) -> Decimal:
    return Decimal(monto).quantize(CENTAVO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Pico:
    """
    La hora de mayor actividad y cuánto concentra.

    `hora` es `None` cuando no hubo un solo movimiento en el periodo. Devolver
    cero —la medianoche— en su lugar afirmaría que el pico es a las 00:00, que
    es una conclusión falsa y, sobre un reporte que se usa para asignar
    personal, una conclusión caprichosa.

    `empates` lista las demás horas con el mismo conteo. Un reporte que
    anuncia «la hora pico es la de las 13:00» cuando las 13:00 y las 19:00
    tienen idéntico volumen oculta la mitad del problema: son dos turnos a
    cubrir, no uno.
    """

    hora: int | None
    conteo: int
    porcentaje: float
    empates: tuple[int, ...] = ()

    @property
    def etiqueta(self) -> str:
        """La hora como se escribe en el reporte: `13:00 h`."""
        return "—" if self.hora is None else f"{self.hora:02d}:00 h"

    @property
    def hay_empate(self) -> bool:
        return bool(self.empates)


def porcentaje(parte, total) -> float:
    """
    Proporción en porcentaje, con un decimal.

    Un total de cero devuelve 0.0 y no lanza: el reporte de un día sin
    movimiento es un reporte válido —dice que no hubo movimiento— y no debe
    hacer caer la pantalla.
    """
    total = Decimal(total or 0)
    if total == 0:
        return 0.0
    return round(float(Decimal(parte or 0) / total * 100), 1)


def hora_pico(conteos_por_hora: list[int]) -> Pico:
    """
    Identifica la hora de mayor actividad de un día tipo de 24 posiciones.

    `conteos_por_hora[h]` es el número de movimientos registrados a la hora
    `h`, acumulado sobre todos los días del periodo. Se exige el vector
    completo de 24 posiciones, incluidas las horas en cero: una lista corta
    desplazaría los índices y señalaría la hora equivocada.
    """
    if len(conteos_por_hora) != 24:
        raise ValueError(
            "La hora pico se calcula sobre las 24 horas del día, incluidas "
            f"las vacías; se recibieron {len(conteos_por_hora)}."
        )

    total = sum(conteos_por_hora)
    if total == 0:
        return Pico(hora=None, conteo=0, porcentaje=0.0)

    maximo = max(conteos_por_hora)
    horas = [h for h, conteo in enumerate(conteos_por_hora) if conteo == maximo]

    return Pico(
        hora=horas[0],
        conteo=maximo,
        porcentaje=porcentaje(maximo, total),
        empates=tuple(horas[1:]),
    )


def distribuir_permanencia(minutos: Iterable[int | None]) -> list[dict]:
    """
    Reparte las estancias en los tramos de `TRAMOS_PERMANENCIA`.

    Devuelve siempre los seis tramos, incluidos los vacíos. Omitir los tramos
    en cero haría que el gráfico cambiara de forma entre dos periodos y que
    «no hubo estancias largas» se viera igual que «no se midieron»: dos cosas
    distintas que no deben parecer la misma.

    Acepta cualquier iterable y recorre una sola vez, para que la consulta
    pueda entregarle un cursor en lugar de materializar en memoria los minutos
    de un periodo largo.
    """
    conteos = [0] * len(TRAMOS_PERMANENCIA)

    for valor in minutos:
        if valor is None:
            continue
        for indice, (limite, _) in enumerate(TRAMOS_PERMANENCIA):
            if limite is None or valor <= limite:
                conteos[indice] += 1
                break

    total = sum(conteos)
    return [
        {
            "etiqueta": etiqueta,
            "conteo": conteos[indice],
            "porcentaje": porcentaje(conteos[indice], total),
        }
        for indice, (_, etiqueta) in enumerate(TRAMOS_PERMANENCIA)
    ]


def ticket_promedio(total, visitas: int) -> Decimal:
    """Gasto medio por visita. Cero visitas devuelve cero, no una división."""
    if not visitas:
        return Decimal("0.00")
    return _redondear(Decimal(total or 0) / Decimal(visitas))


def escala(valores, divisiones: int = 4) -> Decimal:
    """
    Tope «redondo» para el eje de un gráfico.

    Un eje que termina en 1 837.44 obliga a leer la etiqueta para entender la
    altura de cada barra; uno que termina en 2 000 se lee de un vistazo. Se
    elige el menor múltiplo de 1, 2 o 5 por potencia de diez que cubra el
    máximo, de modo que las divisiones intermedias también caigan en cifras
    legibles.

    Devuelve 1 cuando no hay datos o todos son cero: un eje de altura cero
    produciría una división por cero al dibujar.
    """
    maximo = max((Decimal(v or 0) for v in valores), default=Decimal("0"))
    if maximo <= 0:
        return Decimal("1")

    # Se busca el paso de la cuadrícula y el tope se deriva de él, en lugar de
    # redondear el tope y dividirlo: así las divisiones son exactas y no
    # aparecen etiquetas como «458.33» en un eje supuestamente redondo.
    objetivo = maximo / Decimal(divisiones)
    potencia = Decimal(10) ** (len(str(int(objetivo))) - 1 if objetivo >= 1 else 0)

    for factor in (Decimal(1), Decimal(2), Decimal(5), Decimal(10)):
        paso = factor * potencia
        if paso * divisiones >= maximo:
            return paso * divisiones

    return maximo

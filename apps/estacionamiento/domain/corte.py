"""
Motor de corte de caja.

Como el motor de tarifas, es **lógica pura**: recibe importes y devuelve el
resumen con la diferencia. No conoce Django, ni el ORM, ni la interfaz. Esto
permite verificar exhaustivamente el cálculo del faltante —la cifra que
sustenta una conversación incómoda con un operador— sin levantar nada.

Modelo de control aplicado: **cierre ciego**.

El operador declara el efectivo que contó físicamente SIN conocer el total que
el sistema esperaba. Solo después se revela la diferencia. Es el control
interno estándar contra la fuga de ingresos: si el operador ve primero el
total esperado, el conteo tiende a ajustarse a esa cifra y el faltante nunca
aparece. La diferencia deja de ser un dato y se vuelve un trámite.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

CENTAVO = Decimal("0.01")


def _redondear(monto: Decimal) -> Decimal:
    return monto.quantize(CENTAVO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ResumenCorte:
    """
    Resultado del corte.

    `diferencia` es la cifra que importa:
      * positiva  -> sobrante (se contó más efectivo del esperado)
      * negativa  -> faltante (falta efectivo en caja)
      * cero      -> cuadra

    `efectivo_declarado` y `diferencia` son `None` mientras el turno está
    abierto: antes del conteo físico no existe diferencia que reportar, y
    devolver cero en su lugar sería afirmar que cuadra sin haber contado.
    """

    fondo_inicial: Decimal
    total_cobrado: Decimal
    efectivo_esperado: Decimal
    folios_cobrados: int
    folios_cortesia: int
    efectivo_declarado: Decimal | None = None
    diferencia: Decimal | None = None

    @property
    def cuadra(self) -> bool:
        """Verdadero solo si ya se contó el efectivo y no hay diferencia."""
        return self.diferencia is not None and self.diferencia == Decimal("0.00")

    @property
    def hay_faltante(self) -> bool:
        return self.diferencia is not None and self.diferencia < 0

    @property
    def hay_sobrante(self) -> bool:
        return self.diferencia is not None and self.diferencia > 0

    def a_dict(self) -> dict:
        """Representación serializable, base del comprobante de corte."""
        return {
            "fondo_inicial": str(self.fondo_inicial),
            "total_cobrado": str(self.total_cobrado),
            "efectivo_esperado": str(self.efectivo_esperado),
            "folios_cobrados": self.folios_cobrados,
            "folios_cortesia": self.folios_cortesia,
            "efectivo_declarado": (
                str(self.efectivo_declarado)
                if self.efectivo_declarado is not None
                else None
            ),
            "diferencia": (
                str(self.diferencia) if self.diferencia is not None else None
            ),
            "cuadra": self.cuadra,
        }


def calcular_corte(
    *,
    fondo_inicial: Decimal,
    importes_cobrados: Sequence[Decimal],
    efectivo_declarado: Decimal | None = None,
) -> ResumenCorte:
    """
    Calcula el corte a partir del fondo inicial y los importes del turno.

    `importes_cobrados` son los importes de los folios cerrados durante el
    turno. Los de valor cero (cortesías) se cuentan aparte: no mueven efectivo
    pero sí son operación, y su volumen es un indicador de control —un turno
    con muchas cortesías merece revisión—.

    El efectivo esperado es fondo inicial más lo cobrado. Si el operador aún
    no declara su conteo, la diferencia queda indeterminada (`None`), no cero.
    """
    cobros = [Decimal(importe) for importe in importes_cobrados]

    con_cobro = [importe for importe in cobros if importe > 0]
    cortesias = len(cobros) - len(con_cobro)

    total_cobrado = sum(con_cobro, Decimal("0.00"))
    efectivo_esperado = Decimal(fondo_inicial) + total_cobrado

    diferencia = None
    if efectivo_declarado is not None:
        diferencia = _redondear(Decimal(efectivo_declarado) - efectivo_esperado)

    return ResumenCorte(
        fondo_inicial=_redondear(Decimal(fondo_inicial)),
        total_cobrado=_redondear(total_cobrado),
        efectivo_esperado=_redondear(efectivo_esperado),
        folios_cobrados=len(con_cobro),
        folios_cortesia=cortesias,
        efectivo_declarado=(
            _redondear(Decimal(efectivo_declarado))
            if efectivo_declarado is not None
            else None
        ),
        diferencia=diferencia,
    )


# --- Corrección de un corte ya firmado --------------------------------------
#
# El corte es inmutable por diseño: una vez cerrado, `calcular_resumen`
# devuelve su fotografía y no un recálculo. Esa inmutabilidad es correcta y no
# se toca. Lo que faltaba era la otra mitad del control: qué hacer cuando la
# cifra declarada estaba mal.
#
# Ocurre, y de hecho ocurrió: un turno se cerró con $140 menos de los que
# había en caja. Sin forma de corregirlo, la única salida era dejar en el
# histórico un faltante que nunca existió —con lo que la diferencia deja de
# significar algo y el control entero pierde sentido— o editar la base de
# datos a mano, sin registro de quién lo hizo.
#
# La corrección se modela como un **asiento de ajuste**, no como una
# reescritura: el declarado original se conserva, el ajuste se suma aparte y
# el comprobante muestra las dos cifras. Es el mismo criterio que la
# contabilidad aplica a un asiento equivocado —no se borra, se contra-asienta—
# y por el mismo motivo: un faltante que puede desaparecer sin dejar rastro no
# disuade a nadie.

# Caracteres mínimos de la justificación. No es un número arbitrario: por
# debajo de esto solo caben respuestas que no explican nada —«error», «ok»,
# «ajuste»— y una bitácora llena de ellas no sirve para auditar.
MINIMO_JUSTIFICACION = 10


@dataclass(frozen=True)
class ResultadoAjuste:
    """
    Efecto de una corrección sobre un corte cerrado.

    Conserva las dos cifras a propósito: `efectivo_anterior` es lo que el
    operador declaró al cerrar y `efectivo_nuevo` lo que realmente había.
    Mostrar solo la segunda convertiría el ajuste en un borrado.

    `monto` es lo que el ajuste mueve: positivo si apareció efectivo que no se
    había declarado —el caso del faltante corregido— y negativo si se había
    declarado efectivo que no estaba.
    """

    efectivo_esperado: Decimal
    efectivo_anterior: Decimal
    efectivo_nuevo: Decimal
    monto: Decimal
    diferencia_anterior: Decimal
    diferencia_nueva: Decimal

    @property
    def sin_efecto(self) -> bool:
        """
        Verdadero si el ajuste no cambia ninguna cifra.

        Un asiento que no mueve nada no es una corrección: es una línea de
        bitácora que estorba al revisarla. Quien decide qué hacer con esto es
        la capa de aplicación; aquí solo se constata.
        """
        return self.monto == Decimal("0.00")

    @property
    def cuadra(self) -> bool:
        """Si tras el ajuste el corte queda sin diferencia."""
        return self.diferencia_nueva == Decimal("0.00")

    @property
    def acerca_a_cuadrar(self) -> bool:
        """
        Si el ajuste reduce la diferencia en lugar de agrandarla.

        Un ajuste que aleja el corte de cuadrar es legítimo —puede que el
        conteo original estuviera inflado— pero merece destacarse en la
        bitácora: es el patrón que tendría un desvío documentado como
        corrección.
        """
        return abs(self.diferencia_nueva) < abs(self.diferencia_anterior)

    def a_dict(self) -> dict:
        """Representación serializable, para anexar al resumen del turno."""
        return {
            "efectivo_esperado": str(self.efectivo_esperado),
            "efectivo_anterior": str(self.efectivo_anterior),
            "efectivo_nuevo": str(self.efectivo_nuevo),
            "monto": str(self.monto),
            "diferencia_anterior": str(self.diferencia_anterior),
            "diferencia_nueva": str(self.diferencia_nueva),
            "cuadra": self.cuadra,
        }


def calcular_ajuste(
    *,
    efectivo_esperado: Decimal,
    efectivo_declarado_vigente: Decimal,
    efectivo_corregido: Decimal,
) -> ResultadoAjuste:
    """
    Calcula el efecto de corregir el efectivo declarado de un corte.

    `efectivo_declarado_vigente` es la cifra que el corte tiene **ahora**: la
    del cierre si no se ha ajustado nunca, o la del último ajuste si ya se
    ajustó. Se parte de la vigente y no de la original para que dos ajustes
    sucesivos se acumulen en lugar de pisarse —el segundo corrige el estado
    real, no una cifra que ya nadie reclama—.

    No valida nada ni lanza excepciones: es lógica pura y reutilizable. Que un
    ajuste de cero esté permitido o no es una decisión de la capa de
    aplicación, que la toma consultando `sin_efecto`.
    """
    esperado = _redondear(Decimal(efectivo_esperado))
    anterior = _redondear(Decimal(efectivo_declarado_vigente))
    nuevo = _redondear(Decimal(efectivo_corregido))

    return ResultadoAjuste(
        efectivo_esperado=esperado,
        efectivo_anterior=anterior,
        efectivo_nuevo=nuevo,
        monto=_redondear(nuevo - anterior),
        diferencia_anterior=_redondear(anterior - esperado),
        diferencia_nueva=_redondear(nuevo - esperado),
    )


def justificacion_suficiente(texto: str) -> bool:
    """
    Si el texto explica el ajuste o solo rellena el campo.

    Vive en el dominio y no en el formulario porque la regla debe aplicarse
    igual venga de la pantalla, de la API o de un comando: un ajuste sin
    explicación no es aceptable por ninguna vía de entrada.
    """
    return len((texto or "").strip()) >= MINIMO_JUSTIFICACION

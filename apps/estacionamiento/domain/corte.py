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

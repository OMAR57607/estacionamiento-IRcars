"""
Filtros de presentación monetaria para las plantillas.

Centralizar el formato cumple dos propósitos:

1. **Uniformidad.** Un importe mostrado con distinto formato en dos pantallas
   siembra dudas sobre la cifra, y en una caja esa duda cuesta tiempo frente al
   cliente.
2. **Corrección del signo.** La diferencia de un corte es negativa cuando hay
   faltante. Interpolarla sin más produce «FALTANTE de $-10.00», con doble
   negativo, en un documento que el operador firma. Estos filtros separan la
   magnitud del signo para que cada pantalla muestre lo que corresponde.
"""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _a_decimal(valor) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        return Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def pesos(valor) -> str:
    """
    Importe con símbolo y separador de miles: `$1,234.50`.

    Un valor ausente se muestra como raya, no como `$0.00`: no es lo mismo
    «no hay dato» que «el importe es cero», y confundirlos en un corte
    ocultaría un faltante total.
    """
    monto = _a_decimal(valor)
    if monto is None:
        return "—"
    return f"${monto:,.2f}"


@register.filter
def pesos_absoluto(valor) -> str:
    """
    Magnitud del importe, sin signo: `$10.00` incluso si el valor es −10.00.

    Se usa cuando la etiqueta ya comunica la dirección —«FALTANTE de $10.00»—
    y repetir el signo resultaría en un doble negativo confuso.
    """
    monto = _a_decimal(valor)
    if monto is None:
        return "—"
    return f"${abs(monto):,.2f}"


@register.filter
def pesos_con_signo(valor) -> str:
    """
    Importe con signo explícito: `+$50.00` / `-$50.00` / `$0.00`.

    El signo se muestra siempre, y no solo el color, porque hay operadores que
    no distinguen el matiz y porque el comprobante se imprime en blanco y negro.
    """
    monto = _a_decimal(valor)
    if monto is None:
        return "—"
    if monto > 0:
        return f"+${monto:,.2f}"
    if monto < 0:
        return f"-${abs(monto):,.2f}"
    return "$0.00"


@register.filter
def duracion(minutos) -> str:
    """Permanencia legible: `2 h 35 min`. Los minutos se muestran siempre."""
    if minutos is None or minutos == "":
        return "—"
    try:
        total = int(minutos)
    except (TypeError, ValueError):
        return "—"
    if total < 60:
        return f"{total} min"
    horas, resto = divmod(total, 60)
    return f"{horas} h" if resto == 0 else f"{horas} h {resto} min"

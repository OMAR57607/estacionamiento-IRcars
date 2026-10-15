"""
Utilidades de presentación para la interfaz de caja.

Se concentran aquí para que el formato del dinero y del tiempo sea uniforme en
toda la aplicación. Un importe mostrado con distinto formato en dos pantallas
siembra dudas sobre la cifra, y en una caja esa duda cuesta tiempo frente al
cliente.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.utils import timezone


def moneda(monto: Decimal | None) -> str:
    """Formato monetario con separador de miles: `$1,234.50`."""
    if monto is None:
        return "—"
    return f"${Decimal(monto):,.2f}"


def moneda_con_signo(monto: Decimal | None) -> str:
    """
    Formato para diferencias de corte, con signo explícito.

    El signo se muestra siempre: un `+$50.00` y un `-$50.00` no deben
    distinguirse solo por el color, que un operador puede no percibir o una
    impresora en blanco y negro puede no reproducir.
    """
    if monto is None:
        return "—"
    monto = Decimal(monto)
    signo = "+" if monto > 0 else ("-" if monto < 0 else "")
    return f"{signo}${abs(monto):,.2f}"


def duracion(minutos: int | None) -> str:
    """
    Permanencia en formato legible: `2 h 35 min`.

    Se muestran siempre los minutos junto a las horas: el cobro es por fracción
    proporcional y el cliente necesita ver el dato exacto que lo origina.
    """
    if minutos is None:
        return "—"
    minutos = int(minutos)
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    if resto == 0:
        return f"{horas} h"
    return f"{horas} h {resto} min"


def fecha_hora(momento: datetime | None) -> str:
    """Marca de tiempo en la zona horaria local configurada."""
    if momento is None:
        return "—"
    return timezone.localtime(momento).strftime("%d/%m/%Y %H:%M")


def hora(momento: datetime | None) -> str:
    """Solo la hora: suficiente en el tablero de vehículos del día."""
    if momento is None:
        return "—"
    return timezone.localtime(momento).strftime("%H:%M")


def a_decimal(texto: str, *, campo: str = "monto") -> Decimal:
    """
    Convierte la captura del operador a `Decimal`.

    Tolera las formas en que realmente se teclea un monto —con signo de pesos,
    con comas de miles, con espacios— porque rechazar `$1,200.00` por su
    formato sería un obstáculo gratuito frente al cliente. Lo que no se tolera
    es un valor ambiguo: ahí se exige corrección explícita.
    """
    limpio = (texto or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not limpio:
        raise ValueError(f"Capture el {campo}.")
    try:
        valor = Decimal(limpio)
    except Exception as exc:
        raise ValueError(f"El {campo} «{texto}» no es una cantidad válida.") from exc
    if valor < 0:
        raise ValueError(f"El {campo} no puede ser negativo.")
    # Dos decimales: la unidad mínima del peso. Truncar aquí evita que un
    # tercer decimal capturado por error se arrastre al corte.
    return valor.quantize(Decimal("0.01"))

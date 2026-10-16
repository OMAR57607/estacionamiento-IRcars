"""
El cuadro tarifario dicho en palabras.

Nace de un cobro mal configurado en producción: el precio tenía «bloques de
15 minutos» donde debía tener «15 minutos de tolerancia». Los dos campos se
teclean en la misma pantalla, los dos llevan un 15, y ninguna de las dos
capturas es inválida —el motor cobró exactamente lo que se le pidió—, así que
nada falló: simplemente se cobraba otra cosa.

Una tabla de ejemplos no bastó para detectarlo, porque obliga a deducir la
regla a partir de ocho importes. Estas frases la dicen. Si lo que se lee no
es lo que se quería cobrar, el error salta antes de guardar.

Módulo sin Django a propósito: lo usan la caja web y la de escritorio, y una
frase que describa mal el cobro es tan grave como un importe mal calculado,
de modo que se prueba como lógica pura.
"""
from __future__ import annotations

from .domain.tarifas import ParametrosTarifa


def _pesos(valor) -> str:
    return f"${valor:,.2f}"


def en_palabras(parametros: ParametrosTarifa) -> list[str]:
    """Describe el cobro en frases, en el mismo orden en que se aplica."""
    frases = [f"Se cobran {_pesos(parametros.cuota_hora)} por cada hora."]

    if parametros.minutos_cortesia:
        frases.append(
            f"Los primeros {parametros.minutos_cortesia} minutos no se cobran."
        )
    else:
        frases.append("Se cobra desde el primer minuto: no hay minutos gratis.")

    if parametros.minutos_tolerancia:
        frases.append(
            f"Pasada cada hora cumplida hay {parametros.minutos_tolerancia} minutos "
            "de tolerancia: dentro de ellos se cobra el tiempo transcurrido y, al "
            "rebasarlos, la hora completa."
        )
    else:
        frases.append(
            "No hay tolerancia por hora: el tiempo se cobra en proporción y nunca "
            "salta a la hora completa."
        )

    if parametros.fraccion_minutos == 1:
        frases.append("El tiempo se cobra al minuto exacto.")
    else:
        frases.append(
            f"El tiempo se redondea hacia arriba en bloques de "
            f"{parametros.fraccion_minutos} minutos."
        )

    if parametros.cobro_minimo:
        frases.append(f"Nunca se cobra menos de {_pesos(parametros.cobro_minimo)}.")

    if parametros.tope_diario is not None:
        frases.append(
            f"Nunca más de {_pesos(parametros.tope_diario)} por cada día iniciado."
        )

    return frases

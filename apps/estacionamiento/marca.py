"""
Nombre comercial del negocio, resuelto en un solo lugar.

Hasta ahora el nombre vivía en tres sitios a la vez: la variable
`IRCARS_NOMBRE_COMERCIAL` —que ve el cliente en su boleto—, el título de la
plantilla de la caja y la barra superior, estas dos escritas a mano. Al
cambiar la marca, las dos escritas a mano se quedaron atrás: el cliente leía
un nombre y el operador otro. Esa es la clase de desvío que no rompe ninguna
prueba funcional y solo se nota cuando alguien lo lee en la caseta.

Con una sola función, cambiar el nombre vuelve a ser cambiar una variable de
entorno.
"""
from __future__ import annotations

from django.conf import settings

# Genérico a propósito: si alguien despliega sin configurar nada, el boleto
# dice «Estacionamiento» y no el nombre de otro negocio.
PREDETERMINADO = "Estacionamiento"


def nombre_comercial() -> str:
    """Nombre del negocio tal como debe mostrarse a clientes y operadores."""
    configurado = str(getattr(settings, "IRCARS_NOMBRE_COMERCIAL", "")).strip()
    return configurado or PREDETERMINADO


def contexto(request) -> dict:
    """
    Procesador de contexto: expone `nombre_comercial` a todas las plantillas.

    Se resuelve por petición y no al arrancar: así una prueba puede alterar
    el ajuste con `settings.IRCARS_NOMBRE_COMERCIAL` y obtener el efecto real,
    en lugar de un valor congelado en la importación del módulo.
    """
    return {"nombre_comercial": nombre_comercial()}

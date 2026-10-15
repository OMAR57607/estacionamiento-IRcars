"""
Presentación de errores en la interfaz de caja.

Un operador frente a un cliente no puede interpretar una traza de Python. Este
módulo traduce cualquier fallo a un diálogo accionable, y garantiza que nada
quede sin registrar en la bitácora: lo que el operador ve es un mensaje breve,
lo que queda en el archivo es el detalle técnico para diagnosticar después.
"""
from __future__ import annotations

import functools
import logging
from tkinter import messagebox

from apps.estacionamiento.domain.exceptions import ErrorDominio

logger = logging.getLogger(__name__)


def mostrar_error(titulo: str, mensaje: str, padre=None) -> None:
    messagebox.showerror(titulo, mensaje, parent=padre)


def operacion(titulo: str):
    """
    Decorador para los manejadores de botones.

    Distingue dos clases de fallo, porque exigen respuestas distintas:

    * **Error de dominio o de captura.** Es una situación prevista —placa ya
      dentro, pago insuficiente, turno cerrado—. Se muestra el mensaje de
      negocio tal cual: es informativo y suficiente para que el operador actúe.
    * **Error inesperado.** Es un defecto del programa o una falla del equipo.
      Se registra con traza completa en la bitácora y al operador se le indica
      dónde está ese archivo, para que el soporte pueda diagnosticarlo sin
      depender de su memoria de lo ocurrido.

    En ningún caso la excepción escapa: una ventana de caja que se cierra por
    un error deja la operación detenida y al cliente esperando.
    """

    def decorador(funcion):
        @functools.wraps(funcion)
        def envoltura(self, *args, **kwargs):
            try:
                return funcion(self, *args, **kwargs)
            except (ErrorDominio, ValueError) as exc:
                mostrar_error(titulo, str(exc), padre=getattr(self, "winfo_toplevel", lambda: None)())
                return None
            except Exception:
                logger.exception("Error inesperado en %s", funcion.__qualname__)
                from .arranque import ruta_bitacora

                mostrar_error(
                    titulo,
                    "Ocurrió un error inesperado. La operación no se completó.\n\n"
                    f"El detalle quedó registrado en:\n{ruta_bitacora()}",
                    padre=getattr(self, "winfo_toplevel", lambda: None)(),
                )
                return None

        return envoltura

    return decorador

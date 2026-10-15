"""
Confirmación de identidad antes de una acción delicada.

Hay dos operaciones en la aplicación que no pueden depender de un solo clic
—cambiar el precio y borrar movimientos—, y ambas necesitan la misma puerta:
volver a teclear la contraseña del operador que está dentro.

Se pide la contraseña del **propio operador** y no una clave de supervisor
aparte. Una clave de supervisor acaba escrita en un papel pegado al monitor,
que es peor que no tenerla; así, además, la acción queda atribuida a quien se
autenticó y la bitácora dice de quién fue.

El diálogo vive aquí y no dentro de una pestaña porque lo usan dos pantallas
distintas: duplicarlo garantizaría que algún día una de las dos copias
validara de forma distinta que la otra.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from django.contrib.auth import authenticate

from . import tema


class DialogoConfirmacion(tk.Toplevel):
    """
    Pide la contraseña y deja el resultado en `autorizado`.

    El llamador espera con `wait_window` y consulta el atributo. No lanza
    excepciones: cancelar es una respuesta legítima, no un error.
    """

    def __init__(
        self,
        padre,
        operador,
        *,
        titulo: str,
        explicacion: str,
        boton: str = "Confirmar",
    ):
        super().__init__(padre)
        self.autorizado = False
        self._operador = operador

        self.title(titulo)
        self.configure(background=tema.SUPERFICIE)
        self.resizable(False, False)
        self.transient(padre)
        self.grab_set()

        marco = ttk.Frame(self, style="Tarjeta.TFrame", padding=tema.ESPACIO_XL)
        marco.grid(sticky="nsew")
        marco.columnconfigure(0, weight=1)

        ttk.Label(marco, text=titulo, style="Titulo.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            marco,
            text=explicacion,
            style="Secundario.TLabel",
            justify="left",
            wraplength=380,
        ).grid(row=1, column=0, sticky="w", pady=(tema.ESPACIO_S, tema.ESPACIO_L))

        ttk.Label(
            marco,
            text=f"Contraseña de «{operador}»",
            style="Secundario.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, tema.ESPACIO_XS))
        self.clave = ttk.Entry(marco, show="•", font=tema.fuente("captura"), width=26)
        self.clave.grid(row=3, column=0, sticky="ew")
        self.clave.bind("<Return>", lambda _e: self._confirmar())

        self.error = ttk.Label(marco, text="", style="Peligro.TLabel")
        self.error.grid(row=4, column=0, sticky="w", pady=(tema.ESPACIO_S, 0))

        botones = ttk.Frame(marco, style="Tarjeta.TFrame")
        botones.grid(row=5, column=0, sticky="ew", pady=(tema.ESPACIO_L, 0))
        botones.columnconfigure(0, weight=1)
        ttk.Button(
            botones, text="Cancelar", style="Secundario.TButton", command=self.destroy
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            botones,
            text=boton,
            style="Principal.TButton",
            command=self._confirmar,
        ).grid(row=0, column=1, sticky="e")

        self.bind("<Escape>", lambda _e: self.destroy())
        self._centrar(padre)
        self.clave.focus_force()

    def _confirmar(self) -> None:
        clave = self.clave.get()
        if not clave:
            self.error.configure(text="Capture la contraseña.")
            return

        usuario = authenticate(
            username=getattr(self._operador, "username", ""), password=clave
        )
        if usuario is None:
            self.error.configure(text="Contraseña incorrecta.")
            self.clave.delete(0, tk.END)
            self.clave.focus_set()
            return

        self.autorizado = True
        self.destroy()

    def _centrar(self, padre) -> None:
        self.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_rooty() + (padre.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def confirmar_identidad(
    padre, operador, *, titulo: str, explicacion: str, boton: str = "Confirmar"
) -> bool:
    """Abre el diálogo y devuelve si el operador confirmó su contraseña."""
    dialogo = DialogoConfirmacion(
        padre, operador, titulo=titulo, explicacion=explicacion, boton=boton
    )
    padre.wait_window(dialogo)
    return dialogo.autorizado

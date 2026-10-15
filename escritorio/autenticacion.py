"""
Acceso a la aplicación de caja.

Cada movimiento con efecto económico queda atribuido a una persona, y el corte
de caja se le imputa a ella. Por eso el acceso es obligatorio incluso en una
instalación de un solo equipo: sin operador identificado, un faltante no tiene
responsable y el control interno es decorativo.

En el primer arranque no existe ningún usuario, así que la aplicación guía la
creación del primer operador —que recibe perfil administrativo, pues alguien
debe poder configurar la tarifa—.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password

LONGITUD_MINIMA_CLAVE = 8


def existen_operadores() -> bool:
    return get_user_model().objects.exists()


def crear_primer_operador(usuario: str, clave: str):
    """
    Crea el operador inicial con perfil administrativo.

    Recibe permisos de administración porque es el único usuario que existe y
    alguien debe poder dar de alta la tarifa y a los demás operadores. Los
    operadores que él cree después no tendrán ese perfil: el cuadro tarifario
    no debe ser modificable desde una caja.
    """
    usuario = (usuario or "").strip()
    if not usuario:
        raise ValueError("Capture un nombre de usuario.")
    if len(clave or "") < LONGITUD_MINIMA_CLAVE:
        raise ValueError(
            f"La contraseña debe tener al menos {LONGITUD_MINIMA_CLAVE} caracteres."
        )
    try:
        # Se aplican los validadores de Django: rechaza contraseñas comunes y
        # puramente numéricas, los dos casos que un operador elige por defecto.
        validate_password(clave)
    except ValidationError as exc:
        raise ValueError(" ".join(exc.messages)) from exc

    Usuario = get_user_model()
    if Usuario.objects.filter(username__iexact=usuario).exists():
        raise ValueError(f"El usuario «{usuario}» ya existe.")

    return Usuario.objects.create_user(
        username=usuario, password=clave, is_staff=True, is_superuser=True
    )


class DialogoAcceso(tk.Toplevel):
    """
    Ventana modal de acceso.

    Al cerrarse deja el operador autenticado en `self.operador`, o `None` si el
    usuario canceló. El llamador debe terminar la aplicación en ese caso: una
    caja sin operador identificado no debe operar.
    """

    def __init__(self, padre: tk.Misc, *, primer_uso: bool):
        super().__init__(padre)
        self.operador = None
        self._primer_uso = primer_uso

        self.title("IRcars — Acceso" if not primer_uso else "IRcars — Primer operador")
        self.resizable(False, False)
        self.transient(padre)
        self.grab_set()

        self._construir()
        self._centrar()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.entrada_usuario.focus_set()

    # -- Construcción ------------------------------------------------------
    def _construir(self) -> None:
        marco = ttk.Frame(self, padding=20)
        marco.grid(sticky="nsew")

        if self._primer_uso:
            ttk.Label(
                marco,
                text=(
                    "No hay operadores registrados.\n"
                    "Cree el primer usuario administrador."
                ),
                justify="left",
            ).grid(row=0, column=0, columnspan=2, pady=(0, 12), sticky="w")

        ttk.Label(marco, text="Usuario:").grid(row=1, column=0, sticky="w", pady=4)
        self.entrada_usuario = ttk.Entry(marco, width=26)
        self.entrada_usuario.grid(row=1, column=1, pady=4)

        ttk.Label(marco, text="Contraseña:").grid(row=2, column=0, sticky="w", pady=4)
        self.entrada_clave = ttk.Entry(marco, width=26, show="•")
        self.entrada_clave.grid(row=2, column=1, pady=4)

        if self._primer_uso:
            ttk.Label(marco, text="Repetir:").grid(row=3, column=0, sticky="w", pady=4)
            self.entrada_clave2 = ttk.Entry(marco, width=26, show="•")
            self.entrada_clave2.grid(row=3, column=1, pady=4)

        botones = ttk.Frame(marco)
        botones.grid(row=4, column=0, columnspan=2, pady=(16, 0), sticky="e")
        ttk.Button(botones, text="Cancelar", command=self._cancelar).grid(
            row=0, column=0, padx=(0, 8)
        )
        texto = "Crear y entrar" if self._primer_uso else "Entrar"
        principal = ttk.Button(botones, text=texto, command=self._aceptar)
        principal.grid(row=0, column=1)

        # Enter confirma desde cualquier campo: es el gesto natural y evita
        # que el operador busque el botón con el ratón en cada turno.
        for campo in (self.entrada_usuario, self.entrada_clave):
            campo.bind("<Return>", lambda _e: self._aceptar())
        if self._primer_uso:
            self.entrada_clave2.bind("<Return>", lambda _e: self._aceptar())

    def _centrar(self) -> None:
        self.update_idletasks()
        ancho, alto = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - ancho) // 2
        y = (self.winfo_screenheight() - alto) // 3
        self.geometry(f"+{x}+{y}")

    # -- Acciones ----------------------------------------------------------
    def _aceptar(self) -> None:
        usuario = self.entrada_usuario.get().strip()
        clave = self.entrada_clave.get()

        if self._primer_uso:
            if clave != self.entrada_clave2.get():
                messagebox.showerror(
                    "Acceso", "Las contraseñas no coinciden.", parent=self
                )
                return
            try:
                self.operador = crear_primer_operador(usuario, clave)
            except ValueError as exc:
                messagebox.showerror("Acceso", str(exc), parent=self)
                return
            self.destroy()
            return

        operador = authenticate(username=usuario, password=clave)
        if operador is None:
            # Mensaje deliberadamente genérico: no revela si el usuario existe,
            # para no facilitar la enumeración de cuentas.
            messagebox.showerror(
                "Acceso", "Usuario o contraseña incorrectos.", parent=self
            )
            self.entrada_clave.delete(0, tk.END)
            self.entrada_clave.focus_set()
            return
        if not operador.is_active:
            messagebox.showerror(
                "Acceso", "Este usuario está desactivado.", parent=self
            )
            return

        self.operador = operador
        self.destroy()

    def _cancelar(self) -> None:
        self.operador = None
        self.destroy()


def solicitar_acceso(padre: tk.Misc):
    """Muestra el diálogo y devuelve el operador autenticado, o `None`."""
    dialogo = DialogoAcceso(padre, primer_uso=not existen_operadores())
    padre.wait_window(dialogo)
    return dialogo.operador

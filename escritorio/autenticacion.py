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

from . import tema

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
        self.configure(background=tema.SUPERFICIE)

        # `transient()` SOLO si la ventana padre es visible.
        #
        # Esto no es un detalle estético. La ventana principal se oculta con
        # `withdraw()` hasta que haya operador autenticado, de modo que este
        # diálogo sería hijo de una ventana invisible. En Windows, un Toplevel
        # transitorio de una ventana oculta no obtiene botón en la barra de
        # tareas, puede abrirse por detrás de las demás y puede no recibir el
        # foco: el operador ve que «no abre» cuando en realidad la aplicación
        # está corriendo con su ventana enterrada.
        if padre is not None and padre.winfo_viewable():
            self.transient(padre)

        self.grab_set()

        self._construir()
        self._centrar()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self._traer_al_frente()
        self.entrada_usuario.focus_set()

    def _traer_al_frente(self) -> None:
        """
        Fuerza que el diálogo sea visible y tenga el foco.

        Al arrancar desde un icono, el operador acaba de interactuar con el
        explorador de archivos, que conserva el primer plano. Sin esta
        insistencia, la ventana de acceso puede quedar detrás y dar la
        impresión de que el programa no arrancó.

        `-topmost` se activa un instante y se libera enseguida: mantenerlo
        permanentemente dejaría el diálogo por encima de todo lo demás, lo que
        resulta molesto y es innecesario una vez que tiene el foco.
        """
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            # La visibilidad es una mejora, no un requisito: si el gestor de
            # ventanas rechaza alguna de estas llamadas, el diálogo sigue
            # siendo funcional y no debe impedirse el arranque.
            pass

    # -- Construcción ------------------------------------------------------
    def _construir(self) -> None:
        """
        Pantalla de acceso con la identidad del sistema.

        Es lo primero que ve el operador al empezar su jornada y lo único que
        hay en pantalla. De ahí la marca arriba y una sola acción evidente: en
        el diseño anterior, «Cancelar» y «Entrar» eran dos botones grises
        idénticos, y el que cierra la aplicación iba primero.
        """
        marco = ttk.Frame(self, padding=(32, 28), style="Tarjeta.TFrame")
        marco.grid(sticky="nsew")
        marco.columnconfigure(0, weight=1)

        ttk.Label(marco, text="IRcars", style="Titulo.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            marco, text="Control de estacionamiento", style="Tenue.TLabel"
        ).grid(row=1, column=0, sticky="w")

        ttk.Separator(marco, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=(16, 18)
        )

        fila = 3
        if self._primer_uso:
            ttk.Label(
                marco,
                text=(
                    "No hay operadores registrados.\n"
                    "Cree el primer usuario administrador."
                ),
                style="Secundario.TLabel",
                justify="left",
            ).grid(row=fila, column=0, pady=(0, 16), sticky="w")
            fila += 1

        ttk.Label(marco, text="Usuario", style="Secundario.TLabel").grid(
            row=fila, column=0, sticky="w"
        )
        self.entrada_usuario = ttk.Entry(marco, width=28)
        self.entrada_usuario.grid(row=fila + 1, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(marco, text="Contraseña", style="Secundario.TLabel").grid(
            row=fila + 2, column=0, sticky="w"
        )
        self.entrada_clave = ttk.Entry(marco, width=28, show="•")
        self.entrada_clave.grid(row=fila + 3, column=0, sticky="ew", pady=(4, 0))
        fila += 4

        if self._primer_uso:
            ttk.Label(
                marco, text="Repetir contraseña", style="Secundario.TLabel"
            ).grid(row=fila, column=0, sticky="w", pady=(12, 0))
            self.entrada_clave2 = ttk.Entry(marco, width=28, show="•")
            self.entrada_clave2.grid(row=fila + 1, column=0, sticky="ew", pady=(4, 0))
            ttk.Label(
                marco,
                text=f"Mínimo {LONGITUD_MINIMA_CLAVE} caracteres.",
                style="Tenue.TLabel",
            ).grid(row=fila + 2, column=0, sticky="w", pady=(4, 0))
            fila += 3

        texto = "Crear y entrar" if self._primer_uso else "Entrar"
        ttk.Button(
            marco, text=texto, style="Principal.TButton", command=self._aceptar
        ).grid(row=fila, column=0, sticky="ew", pady=(22, 0))

        # Cancelar cierra la aplicación, así que no compite visualmente con la
        # acción que el operador quiere: se ofrece, no se propone.
        ttk.Button(
            marco, text="Cancelar", style="Enlace.TButton", command=self._cancelar
        ).grid(row=fila + 1, column=0, pady=(6, 0))

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

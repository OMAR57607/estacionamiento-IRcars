"""
Ventana principal de la aplicación de caja.

Organiza las pestañas y mantiene el **contexto compartido**: el operador
autenticado y su turno abierto. Ese contexto es la única pieza de estado
global, y existe porque dos pestañas distintas —caja y turno— deben coincidir
en si hay turno abierto: si cada una lo consultara por su cuenta, podrían
discrepar y el operador vería información contradictoria.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from apps.estacionamiento import services

from . import arranque
from .vistas.caja import VistaCaja
from .vistas.reportes import VistaReportes
from .vistas.tarifa import VistaTarifa
from .vistas.turno import VistaTurno

TITULO = "IRcars — Control de Estacionamiento"


class Contexto:
    """
    Estado compartido entre pestañas.

    `turno` se resuelve siempre contra la base, nunca se asume: si el turno se
    cerró desde otra parte de la aplicación, el dato en memoria quedaría
    obsoleto y la caja permitiría cobrar contra un turno ya cortado.
    """

    def __init__(self, operador):
        self.operador = operador
        self.turno = None

    def refrescar_turno(self):
        from apps.estacionamiento import services as servicios_operacion

        self.turno = servicios_operacion.obtener_turno_abierto(self.operador)
        return self.turno


class VentanaPrincipal(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()  # Oculta la ventana hasta que haya operador autenticado.

        self.title(TITULO)
        self.geometry("1040x720")
        self.minsize(900, 620)

        self._aplicar_tema()

        from .autenticacion import solicitar_acceso

        operador = solicitar_acceso(self)
        if operador is None:
            # Sin operador identificado no se opera: cada movimiento con efecto
            # económico debe ser atribuible a una persona.
            self.destroy()
            return

        self.contexto = Contexto(operador)
        self.contexto.refrescar_turno()

        self.deiconify()
        self._construir()
        self._registrar_atajos()
        self._verificar_tarifa()

    # ------------------------------------------------------------------ UI
    def _aplicar_tema(self) -> None:
        estilo = ttk.Style(self)
        # `clam` se ve consistente en Windows y Linux; los temas nativos
        # difieren tanto que la pantalla sería irreconocible entre equipos.
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")
        estilo.configure("TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
        estilo.configure("Treeview", rowheight=24)

    def _construir(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.pestanas = ttk.Notebook(self)
        self.pestanas.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))

        self.vista_caja = VistaCaja(self.pestanas, self.contexto)
        self.vista_turno = VistaTurno(self.pestanas, self.contexto)
        self.vista_reportes = VistaReportes(self.pestanas, self.contexto)
        self.vista_tarifa = VistaTarifa(self.pestanas, self.contexto)

        self.pestanas.add(self.vista_caja, text="  Caja  ")
        self.pestanas.add(self.vista_turno, text="  Turno y corte  ")
        self.pestanas.add(self.vista_reportes, text="  Reportes  ")
        self.pestanas.add(self.vista_tarifa, text="  Tarifa  ")
        self.pestanas.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)

        self._construir_barra_estado()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    def _construir_barra_estado(self) -> None:
        barra = ttk.Frame(self, padding=(10, 6))
        barra.grid(row=1, column=0, sticky="ew")
        barra.columnconfigure(1, weight=1)

        ttk.Label(
            barra, text=f"Operador: {self.contexto.operador}", font=("TkDefaultFont", 9)
        ).grid(row=0, column=0, sticky="w")

        # La ruta del archivo de datos se muestra de forma permanente: es lo
        # que el responsable debe respaldar, y de nada sirve si no sabe dónde está.
        ttk.Label(
            barra,
            text=f"Datos: {arranque.ruta_base_datos()}",
            font=("TkDefaultFont", 8),
            foreground="#666666",
        ).grid(row=0, column=1, sticky="e")

    def _registrar_atajos(self) -> None:
        """
        Atajos globales de la caja.

        Se registran en la ventana, no en cada campo: el operador debe poder
        pulsar F5 sin importar dónde esté el cursor.
        """
        self.bind("<F2>", lambda _e: self._en_caja(self.vista_caja.registrar_entrada))
        self.bind("<F4>", lambda _e: self._en_caja(self.vista_caja.cotizar))
        self.bind("<F5>", lambda _e: self._en_caja(self.vista_caja.cobrar))
        self.bind("<F9>", lambda _e: self.vista_caja.refrescar())

    def _en_caja(self, accion) -> None:
        """Ejecuta la acción asegurando que la pestaña de caja esté visible."""
        self.pestanas.select(self.vista_caja)
        accion()

    def _al_cambiar_pestana(self, _evento=None) -> None:
        actual = self.pestanas.nametowidget(self.pestanas.select())
        if hasattr(actual, "al_entrar"):
            actual.al_entrar()

    # ------------------------------------------------------------ Validaciones
    def _verificar_tarifa(self) -> None:
        """
        Comprueba al arrancar que exista tarifa vigente.

        Es la condición sin la cual nada funciona: sin tarifa no se puede
        registrar una entrada ni cobrar. Advertirlo al inicio evita que el
        operador lo descubra con el primer cliente frente a la ventanilla.
        """
        try:
            services.obtener_tarifa_vigente()
        except Exception:
            messagebox.showwarning(
                "Configuración pendiente",
                "No hay una tarifa vigente registrada.\n\n"
                "No será posible registrar entradas ni cobrar salidas hasta que "
                "se configure.\n\n"
                "Vaya a la pestaña «Tarifa» y registre el cuadro tarifario.",
                parent=self,
            )
            self.pestanas.select(self.vista_tarifa)

    def _al_cerrar(self) -> None:
        if self.contexto.refrescar_turno() is not None:
            if not messagebox.askyesno(
                "Salir",
                "Tiene un turno de caja ABIERTO sin cortar.\n\n"
                "Si cierra ahora, el turno seguirá abierto y podrá continuarlo "
                "al volver a entrar.\n\n¿Desea salir?",
                parent=self,
            ):
                return
        self.destroy()

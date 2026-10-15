"""
Ventana principal de la aplicación de caja.

Organiza las pestañas y mantiene el **contexto compartido**: el operador
autenticado y su turno abierto. Ese contexto es la única pieza de estado
global, y existe porque dos pestañas distintas —caja y turno— deben coincidir
en si hay turno abierto: si cada una lo consultara por su cuenta, podrían
discrepar y el operador vería información contradictoria.

La ventana aporta además la **cabecera de estado**, visible desde cualquier
pestaña. Resuelve el fallo de uso más caro del diseño anterior: la condición
que impide cobrar —no tener turno abierto— solo era visible entrando a otra
pestaña, de modo que el operador la descubría con el cliente ya en la
ventanilla. Ahora el estado del turno está siempre a la vista y, si falta, la
propia cabecera lleva a abrirlo de un clic.

La cabecera **no muestra importe alguno del turno en curso**. Es la misma
restricción del cierre ciego que gobierna la pestaña de corte: si el total
cobrado estuviera a la vista, el conteo físico se ajustaría a él.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from apps.estacionamiento import services

from . import arranque, tema
from .vistas.caja import VistaCaja
from .vistas.reportes import VistaReportes
from .vistas.tarifa import VistaTarifa
from .vistas.turno import VistaTurno

TITULO = "IRcars — Control de Estacionamiento"

# Cadencia del reloj de la cabecera. Al minuto basta: es una referencia para
# el operador, no un cronómetro.
INTERVALO_RELOJ_MS = 20_000


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
        # Las vistas se registran aquí para que un cambio de turno producido en
        # una se refleje de inmediato en la cabecera y en las demás, sin que
        # cada una tenga que conocer a las otras.
        self._observadores = []

    def refrescar_turno(self):
        from apps.estacionamiento import services as servicios_operacion

        self.turno = servicios_operacion.obtener_turno_abierto(self.operador)
        return self.turno

    def al_cambiar(self, devolucion) -> None:
        self._observadores.append(devolucion)

    def notificar(self) -> None:
        """Avisa del cambio de turno a quien lo haya pedido."""
        self.refrescar_turno()
        for devolucion in self._observadores:
            try:
                devolucion()
            except Exception:
                # Un observador que falle no debe impedir que los demás se
                # enteren ni tumbar la operación en curso.
                pass


class VentanaPrincipal(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()  # Oculta la ventana hasta que haya operador autenticado.

        self.title(TITULO)
        self.geometry("1240x800")
        self.minsize(1024, 650)

        tema.aplicar(self)

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
        self.contexto.al_cambiar(self._pintar_estado_turno)
        self._pintar_estado_turno()
        self._tictac()
        # Misma razón que en el diálogo de acceso: tras autenticarse, la
        # ventana principal debe quedar al frente y con el foco, no detrás de
        # lo que el operador tuviera abierto.
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self._verificar_tarifa()

    # ------------------------------------------------------------------ UI
    def _construir(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._construir_cabecera()

        contenedor = ttk.Frame(self, style="Fondo.TFrame")
        contenedor.grid(row=1, column=0, sticky="nsew", padx=tema.ESPACIO_L)
        contenedor.columnconfigure(0, weight=1)
        contenedor.rowconfigure(0, weight=1)

        self.pestanas = ttk.Notebook(contenedor)
        self.pestanas.grid(row=0, column=0, sticky="nsew")

        self.vista_caja = VistaCaja(self.pestanas, self.contexto)
        self.vista_turno = VistaTurno(self.pestanas, self.contexto)
        self.vista_reportes = VistaReportes(self.pestanas, self.contexto)
        self.vista_tarifa = VistaTarifa(self.pestanas, self.contexto)

        self.pestanas.add(self.vista_caja, text="Caja")
        self.pestanas.add(self.vista_turno, text="Turno y corte")
        self.pestanas.add(self.vista_reportes, text="Reportes")
        self.pestanas.add(self.vista_tarifa, text="Tarifa")
        self.pestanas.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)

        self._construir_barra_estado()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    def _construir_cabecera(self) -> None:
        """
        Franja superior con la identidad, el operador y el estado del turno.

        El estado del turno ocupa el extremo derecho, donde la vista se dirige
        al terminar de leer la pantalla, y es pulsable: señalar un problema sin
        ofrecer la vía para resolverlo obliga al operador a buscarla.
        """
        cabecera = tk.Frame(self, background=tema.SUPERFICIE)
        cabecera.grid(row=0, column=0, sticky="ew")
        cabecera.columnconfigure(1, weight=1)

        marca = ttk.Frame(cabecera, style="Cabecera.TFrame", padding=(tema.ESPACIO_L, 14))
        marca.grid(row=0, column=0, sticky="w")
        ttk.Label(marca, text="IRcars", style="Titulo.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            marca, text="Control de estacionamiento", style="Tenue.TLabel"
        ).grid(row=1, column=0, sticky="w")

        derecha = ttk.Frame(cabecera, style="Cabecera.TFrame", padding=(tema.ESPACIO_L, 14))
        derecha.grid(row=0, column=2, sticky="e")

        self.reloj = ttk.Label(derecha, text="", style="Secundario.TLabel")
        self.reloj.grid(row=0, column=0, padx=(0, tema.ESPACIO_L))

        operador = ttk.Frame(derecha, style="Cabecera.TFrame")
        operador.grid(row=0, column=1, padx=(0, tema.ESPACIO_M))
        ttk.Label(
            operador,
            text=str(self.contexto.operador),
            style="TLabel",
            font=tema.fuente("cuerpo_fuerte"),
        ).grid(row=0, column=0, sticky="e")
        ttk.Label(operador, text="Operador", style="Tenue.TLabel").grid(
            row=1, column=0, sticky="e"
        )

        self.pildora_turno = tema.Pildora(derecha, "")
        self.pildora_turno.grid(row=0, column=2)
        self.pildora_turno.configure(cursor="hand2")
        self.pildora_turno.bind(
            "<Button-1>", lambda _e: self.pestanas.select(self.vista_turno)
        )

        # Línea divisoria: separa la cabecera del área de trabajo sin recurrir
        # a un cambio de color que compita con las tarjetas.
        tk.Frame(self, background=tema.BORDE, height=1).grid(
            row=0, column=0, sticky="sew"
        )

    def _construir_barra_estado(self) -> None:
        barra = ttk.Frame(self, style="Barra.TFrame", padding=(tema.ESPACIO_L, 8))
        barra.grid(row=2, column=0, sticky="ew")
        barra.columnconfigure(1, weight=1)

        # Los atajos se recuerdan viéndolos. Tenerlos siempre a la vista es lo
        # que hace que el operador deje de usar el ratón.
        ttk.Label(
            barra,
            text="F2 Entrada     F4 Cotizar     F5 Cobrar     F9 Actualizar",
            style="TenueFondo.TLabel",
        ).grid(row=0, column=0, sticky="w")

        # La carpeta de datos se muestra de forma permanente: es lo que el
        # responsable debe respaldar, y de nada sirve si no sabe dónde está. Se
        # muestra la carpeta y no el archivo —la ruta completa desbordaba la
        # barra— y un clic la copia al portapapeles, que es lo que se hace con
        # ella.
        import os

        carpeta = os.path.dirname(arranque.ruta_base_datos())
        self.etiqueta_datos = ttk.Label(
            barra,
            text=f"Datos: {carpeta}",
            style="TenueFondo.TLabel",
            cursor="hand2",
        )
        self.etiqueta_datos.grid(row=0, column=1, sticky="e")
        self.etiqueta_datos.bind("<Button-1>", lambda _e: self._copiar_ruta(carpeta))

    def _copiar_ruta(self, ruta: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(ruta)
        original = self.etiqueta_datos.cget("text")
        self.etiqueta_datos.configure(text="Ruta copiada al portapapeles")
        self.after(2500, lambda: self.etiqueta_datos.configure(text=original))

    # -------------------------------------------------------------- Estado
    def _pintar_estado_turno(self) -> None:
        """Refleja en la cabecera si se puede cobrar o no."""
        turno = self.contexto.turno
        if turno is None:
            self.pildora_turno.actualizar(
                "SIN TURNO — NO SE PUEDE COBRAR",
                fondo=tema.PELIGRO_SUAVE,
                color=tema.PELIGRO,
            )
        else:
            self.pildora_turno.actualizar(
                f"TURNO {turno.pk} ABIERTO",
                fondo=tema.EXITO_SUAVE,
                color=tema.EXITO,
            )

    def _tictac(self) -> None:
        from django.utils import timezone

        self.reloj.configure(text=timezone.localtime().strftime("%d/%m/%Y  %H:%M"))
        self.after(INTERVALO_RELOJ_MS, self._tictac)

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

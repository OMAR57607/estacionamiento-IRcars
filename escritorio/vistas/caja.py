"""
Pestaña de operación: entrada, cotización y cobro.

Es la pantalla donde el operador pasa el turno completo, por lo que su diseño
atiende el uso real, no la elegancia:

* **Teclado antes que ratón.** `F2` registra entrada, `F4` cotiza y `F5` cobra.
  Con un cliente esperando, mover la mano al ratón cuesta segundos en cada
  vehículo.
* **El importe en grande.** La cifra a cobrar es el dato que el operador dice
  en voz alta; se muestra en tipografía grande para que no se lea mal.
* **Cotizar antes de cobrar.** Son dos pasos separados a propósito: el operador
  informa el monto, recibe el efectivo y solo entonces cierra. Un botón único
  que cobrara y cerrara de golpe convertiría cualquier error de captura en un
  folio cerrado e inmutable.
* **Tablero de vehículos dentro.** Permite localizar una placa cuando el
  cliente perdió el boleto, el caso incómodo más frecuente en una caseta.
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from tkinter import messagebox, ttk

from apps.estacionamiento import services
from apps.estacionamiento.domain.exceptions import SinTurnoAbierto

from .. import formato
from ..errores import operacion

# Refresco del tablero. Treinta segundos es suficiente: los minutos que importan
# se recalculan al cotizar, y un refresco agresivo robaría respuesta a la
# captura de placas.
INTERVALO_REFRESCO_MS = 30_000


class VistaCaja(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=12)
        self.contexto = contexto
        self._folio_cotizado = None
        self._cotizacion = None

        self.columnconfigure(0, weight=1, uniform="col")
        self.columnconfigure(1, weight=1, uniform="col")
        self.rowconfigure(1, weight=1)

        self._construir_entrada()
        self._construir_salida()
        self._construir_tablero()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_entrada(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Entrada de vehículo ", padding=12)
        grupo.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        grupo.columnconfigure(1, weight=1)

        ttk.Label(grupo, text="Placa:").grid(row=0, column=0, sticky="w")
        self.placa_entrada = ttk.Entry(grupo, font=("TkDefaultFont", 14), width=14)
        self.placa_entrada.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.placa_entrada.bind("<Return>", lambda _e: self.registrar_entrada())

        ttk.Button(
            grupo, text="Registrar entrada  (F2)", command=self.registrar_entrada
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        self.aviso_entrada = ttk.Label(grupo, text="", foreground="#1b6b2f")
        self.aviso_entrada.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _construir_salida(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Salida y cobro ", padding=12)
        grupo.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        grupo.columnconfigure(1, weight=1)

        ttk.Label(grupo, text="Placa:").grid(row=0, column=0, sticky="w")
        self.placa_salida = ttk.Entry(grupo, font=("TkDefaultFont", 14), width=14)
        self.placa_salida.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.placa_salida.bind("<Return>", lambda _e: self.cotizar())

        ttk.Button(grupo, text="Cotizar  (F4)", command=self.cotizar).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )

        self.detalle_cobro = ttk.Label(grupo, text="", justify="left")
        self.detalle_cobro.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.importe_grande = ttk.Label(
            grupo, text="", font=("TkDefaultFont", 26, "bold")
        )
        self.importe_grande.grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Label(grupo, text="Pago recibido:").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.pago = ttk.Entry(grupo, font=("TkDefaultFont", 14), width=14)
        self.pago.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))
        self.pago.bind("<Return>", lambda _e: self.cobrar())

        self.boton_cobrar = ttk.Button(
            grupo, text="Cobrar y cerrar  (F5)", command=self.cobrar, state="disabled"
        )
        self.boton_cobrar.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        self.aviso_cambio = ttk.Label(
            grupo, text="", font=("TkDefaultFont", 13, "bold"), foreground="#1b6b2f"
        )
        self.aviso_cambio.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _construir_tablero(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Vehículos dentro ", padding=8)
        grupo.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        grupo.columnconfigure(0, weight=1)
        grupo.rowconfigure(0, weight=1)

        columnas = ("placa", "cajon", "entrada", "permanencia")
        self.tabla = ttk.Treeview(
            grupo, columns=columnas, show="headings", selectmode="browse"
        )
        for columna, titulo, ancho in (
            ("placa", "Placa", 140),
            ("cajon", "Cajón", 90),
            ("entrada", "Entrada", 110),
            ("permanencia", "Permanencia", 140),
        ):
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor="w")
        self.tabla.grid(row=0, column=0, sticky="nsew")
        # Doble clic sobre una fila la lleva al campo de salida: es el gesto
        # para atender a quien perdió el boleto.
        self.tabla.bind("<Double-1>", self._usar_fila_seleccionada)

        barra = ttk.Scrollbar(grupo, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=barra.set)
        barra.grid(row=0, column=1, sticky="ns")

        pie = ttk.Frame(grupo)
        pie.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.resumen_tablero = ttk.Label(pie, text="")
        self.resumen_tablero.grid(row=0, column=0, sticky="w")
        ttk.Button(pie, text="Actualizar  (F9)", command=self.refrescar).grid(
            row=0, column=1, sticky="e"
        )
        pie.columnconfigure(0, weight=1)

    # -------------------------------------------------------------- Acciones
    @operacion("Entrada")
    def registrar_entrada(self) -> None:
        placa = self.placa_entrada.get()
        folio = services.registrar_entrada(
            placa=placa, operador=self.contexto.operador
        )
        self.placa_entrada.delete(0, tk.END)
        self.placa_entrada.focus_set()

        ubicacion = f" · Cajón {folio.cajon.codigo}" if folio.cajon else ""
        self.aviso_entrada.configure(
            text=(
                f"✓ {folio.placa} ingresó a las "
                f"{formato.hora(folio.entrada_en)}{ubicacion}"
            )
        )
        self.refrescar()

    @operacion("Cotización")
    def cotizar(self) -> None:
        folio = services.buscar_folio_abierto_por_placa(self.placa_salida.get())
        if folio is None:
            messagebox.showwarning(
                "Cotización",
                "No hay ningún vehículo dentro con esa placa.\n\n"
                "Verifique la captura o búsquela en el tablero de abajo.",
                parent=self.winfo_toplevel(),
            )
            self._limpiar_cobro()
            return

        cotizacion = services.cotizar_folio(folio)
        self._folio_cotizado = folio
        self._cotizacion = cotizacion

        etiqueta = " (cortesía)" if cotizacion.es_cortesia else ""
        lineas = [
            f"Placa: {folio.placa}",
            f"Entrada: {formato.fecha_hora(folio.entrada_en)}",
            f"Permanencia: {formato.duracion(cotizacion.minutos)}{etiqueta}",
            f"Tarifa: {folio.tarifa.nombre}",
        ]
        if cotizacion.tope_diario_aplicado:
            lineas.append("Se aplicó el tope diario.")
        self.detalle_cobro.configure(text="\n".join(lineas))
        self.importe_grande.configure(text=formato.moneda(cotizacion.importe))

        self.boton_cobrar.configure(state="normal")
        self.aviso_cambio.configure(text="")
        self.pago.delete(0, tk.END)
        self.pago.focus_set()

    @operacion("Cobro")
    def cobrar(self) -> None:
        if self._folio_cotizado is None:
            messagebox.showinfo(
                "Cobro",
                "Cotice primero el folio para conocer el importe.",
                parent=self.winfo_toplevel(),
            )
            return

        texto_pago = self.pago.get().strip()
        # El pago es opcional: una cortesía o un cobro con terminal no mueven
        # efectivo, y exigir una cifra obligaría al operador a inventarla.
        pago = formato.a_decimal(texto_pago, campo="pago recibido") if texto_pago else None

        try:
            folio, cotizacion = services.registrar_salida(
                uuid_folio=self._folio_cotizado.uuid,
                pago_recibido=pago,
                operador=self.contexto.operador,
                exigir_turno=True,
            )
        except SinTurnoAbierto as exc:
            # Se eleva a diálogo explícito con la instrucción concreta: el
            # operador debe saber qué hacer, no solo que algo falló.
            messagebox.showwarning(
                "Cobro",
                f"{exc}\n\nVaya a la pestaña «Turno y corte» y abra su turno.",
                parent=self.winfo_toplevel(),
            )
            return

        mensaje = f"✓ Cobrado {formato.moneda(folio.importe)}"
        if folio.cambio is not None and folio.cambio > 0:
            mensaje += f"   ·   CAMBIO: {formato.moneda(folio.cambio)}"
        elif cotizacion.es_cortesia:
            mensaje = "✓ Salida registrada en cortesía (sin cobro)"
        self.aviso_cambio.configure(text=mensaje)

        self._folio_cotizado = None
        self._cotizacion = None
        self.detalle_cobro.configure(text="")
        self.importe_grande.configure(text="")
        self.boton_cobrar.configure(state="disabled")
        self.placa_salida.delete(0, tk.END)
        self.pago.delete(0, tk.END)
        self.placa_entrada.focus_set()
        self.refrescar()

    # ------------------------------------------------------------- Auxiliares
    def _limpiar_cobro(self) -> None:
        self._folio_cotizado = None
        self._cotizacion = None
        self.detalle_cobro.configure(text="")
        self.importe_grande.configure(text="")
        self.aviso_cambio.configure(text="")
        self.boton_cobrar.configure(state="disabled")

    def _usar_fila_seleccionada(self, _evento=None) -> None:
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        placa = self.tabla.item(seleccion[0], "values")[0]
        self.placa_salida.delete(0, tk.END)
        self.placa_salida.insert(0, placa)
        self.cotizar()

    @operacion("Tablero")
    def refrescar(self) -> None:
        """Recarga el tablero y reprograma el siguiente refresco."""
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)

        filas = services.folios_en_patio()
        for fila in filas:
            self.tabla.insert(
                "",
                "end",
                values=(
                    fila["placa"],
                    fila["cajon"],
                    formato.hora(fila["entrada_en"]),
                    formato.duracion(fila["minutos"]),
                ),
            )
        self.resumen_tablero.configure(
            text=f"{len(filas)} vehículo(s) dentro"
        )

        # Reprogramación encadenada en lugar de un hilo: Tkinter no es seguro
        # para accesos desde otros hilos, y `after` mantiene todo en el hilo
        # de la interfaz sin bloquearla.
        self.after(INTERVALO_REFRESCO_MS, self.refrescar)

    def al_entrar(self) -> None:
        """Invocado al seleccionar la pestaña: deja el foco listo para teclear."""
        self.placa_entrada.focus_set()

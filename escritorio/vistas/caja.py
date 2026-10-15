"""
Pestaña de operación: entrada, cotización y cobro.

Es la pantalla donde el operador pasa el turno completo, por lo que su diseño
atiende el uso real, no la elegancia:

* **Teclado antes que ratón.** `F2` registra entrada, `F4` cotiza y `F5` cobra.
  Con un cliente esperando, mover la mano al ratón cuesta segundos en cada
  vehículo.
* **El importe en grande.** La cifra a cobrar es el dato que el operador dice
  en voz alta; se muestra en tipografía grande y ocupa un lugar fijo, visible
  incluso vacío, para que la vista sepa siempre dónde buscarla.
* **El cambio se calcula mientras se teclea.** Antes solo aparecía después de
  cerrar el folio, cuando ya no servía para contar billetes. Ahora el operador
  teclea lo que recibió y ve de inmediato cuánto devolver —o cuánto falta—,
  todavía a tiempo de corregir.
* **Cotizar antes de cobrar.** Son dos pasos separados a propósito: el operador
  informa el monto, recibe el efectivo y solo entonces cierra. Un botón único
  que cobrara y cerrara de golpe convertiría cualquier error de captura en un
  folio cerrado e inmutable.
* **Tablero de vehículos dentro, con búsqueda.** Permite localizar una placa
  cuando el cliente perdió el boleto, el caso incómodo más frecuente en una
  caseta. Con el patio lleno, recorrer la lista con la vista es más lento que
  teclear tres caracteres, así que el filtro va arriba de la tabla.
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import messagebox, ttk

from apps.estacionamiento import services
from apps.estacionamiento.domain.exceptions import SinTurnoAbierto

from .. import formato, tema
from ..errores import operacion

# Refresco del tablero. Treinta segundos es suficiente: los minutos que importan
# se recalculan al cotizar, y un refresco agresivo robaría respuesta a la
# captura de placas.
INTERVALO_REFRESCO_MS = 30_000

# Tiempo que permanece visible un aviso de operación cumplida. Lo bastante para
# leerlo, lo bastante poco para no confundirse con el del vehículo siguiente:
# un «✓ cobrado $45.00» que sobrevive tres clientes es información falsa.
DURACION_AVISO_MS = 12_000

# Denominaciones de uso corriente. Evitan teclear el importe recibido en el
# caso más frecuente, que es que el cliente pague con un billete.
BILLETES = (50, 100, 200, 500)


class VistaCaja(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=tema.ESPACIO_L, style="Fondo.TFrame")
        self.contexto = contexto
        self._folio_cotizado = None
        self._cotizacion = None
        self._filas = []
        self._aviso_entrada_id = None
        self._aviso_cobro_id = None

        # Dos columnas con sentido propio: a la izquierda lo que entra y
        # quién está dentro; a la derecha lo que sale y se cobra. El tablero
        # ocupa el alto sobrante de su columna en lugar de quedar aplastado
        # bajo dos tarjetas, que era el defecto del acomodo anterior.
        self.columnconfigure(0, weight=5, uniform="col")
        self.columnconfigure(1, weight=4, uniform="col")
        self.rowconfigure(1, weight=1)

        self._construir_entrada()
        self._construir_salida()
        self._construir_tablero()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_entrada(self) -> None:
        tarjeta = tema.Tarjeta(self, "Entrada de vehículo")
        tarjeta.grid(row=0, column=0, sticky="new", padx=(0, tema.ESPACIO_M))
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        ttk.Label(cuerpo, text="Placa", style="Secundario.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, tema.ESPACIO_XS)
        )
        self.placa_entrada = ttk.Entry(
            cuerpo, font=tema.fuente("captura"), style="Captura.TEntry"
        )
        self.placa_entrada.grid(row=1, column=0, sticky="ew")
        self.placa_entrada.bind("<Return>", lambda _e: self.registrar_entrada())

        ttk.Label(
            cuerpo,
            text="Se guarda en mayúsculas y sin espacios.",
            style="Tenue.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(tema.ESPACIO_XS, 0))

        ttk.Button(
            cuerpo,
            text="Registrar entrada   ·   F2",
            style="Principal.TButton",
            command=self.registrar_entrada,
        ).grid(row=3, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0))

        # Zona de acuse. Se reserva el espacio desde el principio para que la
        # tarjeta no cambie de alto al aparecer el mensaje: un salto de la
        # interfaz entre un cliente y el siguiente desorienta.
        self.aviso_entrada = tk.Label(
            cuerpo,
            text="",
            background=tema.SUPERFICIE,
            foreground=tema.EXITO,
            font=tema.fuente("cuerpo_fuerte"),
            justify="left",
            anchor="w",
            wraplength=260,
        )
        self.aviso_entrada.grid(row=4, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0))

    def _construir_salida(self) -> None:
        tarjeta = tema.Tarjeta(self, "Salida y cobro")
        tarjeta.grid(row=0, column=1, rowspan=2, sticky="nsew")
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.columnconfigure(1, weight=1)

        ttk.Label(cuerpo, text="Placa", style="Secundario.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, tema.ESPACIO_XS)
        )
        marco_placa = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        marco_placa.grid(row=1, column=0, columnspan=2, sticky="ew")
        marco_placa.columnconfigure(0, weight=1)

        self.placa_salida = ttk.Entry(
            marco_placa, font=tema.fuente("captura"), style="Captura.TEntry"
        )
        self.placa_salida.grid(row=0, column=0, sticky="ew")
        self.placa_salida.bind("<Return>", lambda _e: self.cotizar())

        ttk.Button(
            marco_placa,
            text="Cotizar   ·   F4",
            style="Principal.TButton",
            command=self.cotizar,
        ).grid(row=0, column=1, padx=(tema.ESPACIO_S, 0))

        # --- Resultado de la cotización ------------------------------------
        panel = tk.Frame(
            cuerpo,
            background=tema.SUPERFICIE_ALT,
            highlightbackground=tema.BORDE,
            highlightthickness=1,
            bd=0,
        )
        panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(tema.ESPACIO_M, 0))
        panel.columnconfigure(0, weight=1)

        interior = tk.Frame(panel, background=tema.SUPERFICIE_ALT, padx=14, pady=10)
        interior.grid(row=0, column=0, sticky="ew")
        interior.columnconfigure(0, weight=1)

        self.etiqueta_importe = tk.Label(
            interior,
            text="IMPORTE A COBRAR",
            background=tema.SUPERFICIE_ALT,
            foreground=tema.TEXTO_SEC,
            font=tema.fuente("seccion"),
            anchor="w",
        )
        self.etiqueta_importe.grid(row=0, column=0, sticky="w")

        # El importe existe en pantalla desde antes de haber cotizado, en gris
        # y con un guion. Un hueco vacío no enseña dónde mirar; este sí.
        self.importe_grande = tk.Label(
            interior,
            text="—",
            background=tema.SUPERFICIE_ALT,
            foreground=tema.DESHABILITADO,
            font=tema.fuente("display"),
            anchor="w",
        )
        self.importe_grande.grid(row=1, column=0, sticky="w")

        self.detalle_cobro = tk.Label(
            interior,
            text="Capture la placa y pulse Cotizar para conocer el importe.",
            background=tema.SUPERFICIE_ALT,
            foreground=tema.TEXTO_SEC,
            font=tema.fuente("cuerpo"),
            justify="left",
            anchor="w",
        )
        self.detalle_cobro.grid(row=2, column=0, sticky="w", pady=(tema.ESPACIO_S, 0))

        # --- Pago -----------------------------------------------------------
        ttk.Label(cuerpo, text="Pago recibido", style="Secundario.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(tema.ESPACIO_M, tema.ESPACIO_XS)
        )

        # El campo va en su propia fila y los atajos debajo: compartiendo
        # renglón, los cinco botones estrujaban el campo hasta hacerlo
        # inservible, que es justo donde el operador teclea más rápido.
        self.pago = ttk.Entry(
            cuerpo, font=tema.fuente("captura"), style="Captura.TEntry", width=14
        )
        self.pago.grid(row=4, column=0, sticky="w")
        self.pago.bind("<Return>", lambda _e: self.cobrar())
        # El cambio se recalcula con cada tecla: es el dato que el operador
        # necesita *mientras* cuenta los billetes, no después de cerrar.
        self.pago.bind("<KeyRelease>", lambda _e: self._actualizar_cambio())

        # Reparto a lo ancho en lugar de ancho natural: con cinco botones en
        # fila, el último se salía de la tarjeta.
        atajos = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        atajos.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(tema.ESPACIO_S, 0))
        ttk.Button(
            atajos,
            text="Exacto",
            style="Compacto.TButton",
            command=self._pago_exacto,
        ).grid(row=0, column=0, sticky="ew", padx=(0, tema.ESPACIO_XS))
        atajos.columnconfigure(0, weight=1)
        for indice, billete in enumerate(BILLETES, start=1):
            ttk.Button(
                atajos,
                text=f"${billete}",
                style="Compacto.TButton",
                command=lambda b=billete: self._fijar_pago(Decimal(b)),
            ).grid(row=0, column=indice, sticky="ew", padx=(0, tema.ESPACIO_XS))
            atajos.columnconfigure(indice, weight=1)

        # Resultado del cálculo en vivo: cambio a devolver o faltante.
        self.aviso_cambio = tk.Label(
            cuerpo,
            text="",
            background=tema.SUPERFICIE,
            foreground=tema.EXITO,
            font=tema.fuente("cifra_chica"),
            anchor="w",
        )
        # Junto al campo del pago, no debajo: es la cifra que el operador lee
        # mientras todavía tiene los billetes en la mano.
        self.aviso_cambio.grid(row=4, column=1, sticky="w", padx=(tema.ESPACIO_M, 0))

        self.boton_cobrar = ttk.Button(
            cuerpo,
            text="Cobrar y cerrar folio   ·   F5",
            style="Principal.TButton",
            command=self.cobrar,
            state="disabled",
        )
        # Fila elástica entre los atajos y el botón: ancla la acción principal
        # al pie de la tarjeta, de modo que esté siempre en el mismo punto de
        # la pantalla sin importar cuántas líneas traiga el detalle del folio.
        cuerpo.rowconfigure(6, weight=1)
        self.boton_cobrar.grid(
            row=7, column=0, columnspan=2, sticky="sew", pady=(tema.ESPACIO_M, 0)
        )

    def _construir_tablero(self) -> None:
        tarjeta = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        tarjeta.grid(
            row=1, column=0, sticky="nsew",
            padx=(0, tema.ESPACIO_M), pady=(tema.ESPACIO_M, 0),
        )
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(1, weight=1)

        # --- Encabezado con buscador ---------------------------------------
        encabezado = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew", pady=(0, tema.ESPACIO_S))
        encabezado.columnconfigure(1, weight=1)

        self.titulo_tablero = ttk.Label(
            encabezado, text="VEHÍCULOS DENTRO", style="Seccion.TLabel"
        )
        self.titulo_tablero.grid(row=0, column=0, sticky="w")

        derecha = ttk.Frame(encabezado, style="Tarjeta.TFrame")
        derecha.grid(row=0, column=2, sticky="e")

        ttk.Label(derecha, text="Buscar placa", style="Tenue.TLabel").grid(
            row=0, column=0, padx=(0, tema.ESPACIO_S)
        )
        self.filtro = ttk.Entry(derecha, width=16)
        self.filtro.grid(row=0, column=1)
        self.filtro.bind("<KeyRelease>", lambda _e: self._pintar_filas())
        # Enter en el buscador cotiza directamente la única coincidencia: es el
        # camino completo «perdí el boleto» sin tocar el ratón.
        self.filtro.bind("<Return>", lambda _e: self._cotizar_desde_tablero())

        ttk.Button(
            derecha,
            text="Actualizar  ·  F9",
            style="Secundario.TButton",
            command=self.refrescar,
        ).grid(row=0, column=2, padx=(tema.ESPACIO_S, 0))

        # --- Tabla -----------------------------------------------------------
        self.marco_tabla, self.tabla = tema.tabla_con_barra(
            cuerpo,
            (
                ("placa", "Placa", 160, "w"),
                ("cajon", "Cajón", 90, "w"),
                ("entrada", "Entrada", 110, "w"),
                ("permanencia", "Permanencia", 150, "w"),
                ("importe", "Lleva acumulado", 150, "e"),
            ),
            altura=8,
        )
        self.marco_tabla.grid(row=1, column=0, sticky="nsew")

        # Doble clic o Enter sobre una fila la llevan al campo de salida: es el
        # gesto para atender a quien perdió el boleto.
        self.tabla.bind("<Double-1>", lambda _e: self._cotizar_desde_tablero())
        self.tabla.bind("<Return>", lambda _e: self._cotizar_desde_tablero())

        # Mensaje para el patio vacío. Una tabla en blanco no distingue «no hay
        # nadie dentro» de «no cargó».
        self.vacio = ttk.Label(
            cuerpo,
            text="",
            style="Tenue.TLabel",
            anchor="center",
            justify="center",
        )

        self.resumen_tablero = ttk.Label(cuerpo, text="", style="Tenue.TLabel")
        self.resumen_tablero.grid(row=2, column=0, sticky="w", pady=(tema.ESPACIO_S, 0))

    # -------------------------------------------------------------- Acciones
    @operacion("Entrada")
    def registrar_entrada(self) -> None:
        placa = self.placa_entrada.get()
        folio = services.registrar_entrada(
            placa=placa, operador=self.contexto.operador
        )
        self.placa_entrada.delete(0, tk.END)
        self.placa_entrada.focus_set()

        ubicacion = f"\nCajón {folio.cajon.codigo}" if folio.cajon else ""
        self._mostrar_aviso_entrada(
            f"✓  {folio.placa} ingresó a las {formato.hora(folio.entrada_en)}{ubicacion}"
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

        lineas = [
            f"{folio.placa}   ·   entró {formato.fecha_hora(folio.entrada_en)}",
            f"Permanencia: {formato.duracion(cotizacion.minutos)}   ·   "
            f"Tarifa: {folio.tarifa.nombre}",
        ]
        if cotizacion.es_cortesia:
            lineas.append("Dentro de la cortesía: no se cobra.")
        if cotizacion.tope_diario_aplicado:
            lineas.append("Se aplicó el tope diario.")

        self.etiqueta_importe.configure(
            text="CORTESÍA — SIN COBRO" if cotizacion.es_cortesia else "IMPORTE A COBRAR"
        )
        self.detalle_cobro.configure(text="\n".join(lineas))
        self.importe_grande.configure(
            text=formato.moneda(cotizacion.importe),
            foreground=tema.EXITO if cotizacion.es_cortesia else tema.TEXTO,
        )

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

        if cotizacion.es_cortesia:
            mensaje, color = "✓  Salida en cortesía, sin cobro", tema.EXITO
        elif folio.cambio is not None and folio.cambio > 0:
            mensaje = (
                f"✓  Cobrado {formato.moneda(folio.importe)}   ·   "
                f"CAMBIO A DEVOLVER {formato.moneda(folio.cambio)}"
            )
            color = tema.EXITO
        else:
            mensaje, color = f"✓  Cobrado {formato.moneda(folio.importe)}", tema.EXITO

        self._limpiar_cobro()
        self._mostrar_aviso_cobro(mensaje, color)

        self.placa_salida.delete(0, tk.END)
        self.pago.delete(0, tk.END)
        self.placa_entrada.focus_set()
        self.refrescar()

    # ------------------------------------------------------------- Auxiliares
    def _actualizar_cambio(self) -> None:
        """
        Calcula el cambio conforme el operador teclea.

        Se escribe directamente en la etiqueta y no se eleva ningún error: aquí
        una captura a medias —«1» camino de «100»— es normal, y un diálogo por
        cada tecla sería inutilizable. La validación de verdad ocurre al cobrar.
        """
        if self._cotizacion is None:
            self.aviso_cambio.configure(text="")
            return

        texto = self.pago.get().strip().replace("$", "").replace(",", "").replace(" ", "")
        if not texto:
            self.aviso_cambio.configure(text="")
            return

        try:
            recibido = Decimal(texto)
        except (InvalidOperation, ValueError):
            self.aviso_cambio.configure(
                text="Cantidad no válida", foreground=tema.TEXTO_TENUE
            )
            return

        diferencia = recibido - self._cotizacion.importe
        if diferencia < 0:
            self.aviso_cambio.configure(
                text=f"Faltan {formato.moneda(-diferencia)}", foreground=tema.PELIGRO
            )
        elif diferencia == 0:
            self.aviso_cambio.configure(
                text="Pago exacto · sin cambio", foreground=tema.EXITO
            )
        else:
            self.aviso_cambio.configure(
                text=f"Cambio a devolver: {formato.moneda(diferencia)}",
                foreground=tema.EXITO,
            )

    def _fijar_pago(self, cantidad: Decimal) -> None:
        self.pago.delete(0, tk.END)
        self.pago.insert(0, f"{cantidad:.2f}")
        self._actualizar_cambio()
        self.pago.focus_set()

    def _pago_exacto(self) -> None:
        if self._cotizacion is None:
            return
        self._fijar_pago(self._cotizacion.importe)

    def _mostrar_aviso_entrada(self, mensaje: str) -> None:
        if self._aviso_entrada_id is not None:
            self.after_cancel(self._aviso_entrada_id)
        self.aviso_entrada.configure(text=mensaje)
        self._aviso_entrada_id = self.after(
            DURACION_AVISO_MS, lambda: self.aviso_entrada.configure(text="")
        )

    def _mostrar_aviso_cobro(self, mensaje: str, color: str) -> None:
        if self._aviso_cobro_id is not None:
            self.after_cancel(self._aviso_cobro_id)
        self.aviso_cambio.configure(text=mensaje, foreground=color)
        self._aviso_cobro_id = self.after(
            DURACION_AVISO_MS, lambda: self.aviso_cambio.configure(text="")
        )

    def _limpiar_cobro(self) -> None:
        self._folio_cotizado = None
        self._cotizacion = None
        self.etiqueta_importe.configure(text="IMPORTE A COBRAR")
        self.detalle_cobro.configure(
            text="Capture la placa y pulse Cotizar para conocer el importe."
        )
        self.importe_grande.configure(text="—", foreground=tema.DESHABILITADO)
        self.aviso_cambio.configure(text="")
        self.boton_cobrar.configure(state="disabled")

    def _cotizar_desde_tablero(self) -> None:
        """Lleva al cobro la fila seleccionada, o la única que coincida."""
        seleccion = self.tabla.selection()
        if seleccion:
            placa = self.tabla.item(seleccion[0], "values")[0]
        else:
            visibles = self.tabla.get_children()
            if len(visibles) != 1:
                return
            placa = self.tabla.item(visibles[0], "values")[0]

        self.placa_salida.delete(0, tk.END)
        self.placa_salida.insert(0, placa)
        self.cotizar()

    @operacion("Tablero")
    def refrescar(self) -> None:
        """Recarga el tablero y reprograma el siguiente refresco."""
        self._filas = services.folios_en_patio()
        self._pintar_filas()

        # Reprogramación encadenada en lugar de un hilo: Tkinter no es seguro
        # para accesos desde otros hilos, y `after` mantiene todo en el hilo
        # de la interfaz sin bloquearla.
        self.after(INTERVALO_REFRESCO_MS, self.refrescar)

    def _pintar_filas(self) -> None:
        """
        Vuelca al tablero las filas que pasan el filtro.

        Separado de `refrescar` a propósito: teclear en el buscador no debe
        volver a consultar la base, solo reordenar lo que ya está en memoria.
        """
        patron = self.filtro.get().strip().upper().replace("-", "").replace(" ", "")
        filas = [
            fila
            for fila in self._filas
            if not patron or patron in fila["placa"].upper().replace("-", "")
        ]

        for elemento in self.tabla.get_children():
            self.tabla.delete(elemento)

        # Columna «Cajón» solo si hay inventario de cajones: una columna que
        # siempre dice «—» ocupa el ancho que necesitan las demás.
        hay_cajones = any(fila["cajon"] != "—" for fila in self._filas)
        self.tabla.configure(
            displaycolumns=(
                ("placa", "cajon", "entrada", "permanencia", "importe")
                if hay_cajones
                else ("placa", "entrada", "permanencia", "importe")
            )
        )

        # La tarifa se resuelve una vez por repintado y no una vez por fila: con
        # el patio lleno serían treinta consultas idénticas a cada tecla del
        # buscador.
        parametros = self._parametros_tarifa()

        for indice, fila in enumerate(filas):
            self.tabla.insert(
                "",
                "end",
                values=(
                    fila["placa"],
                    fila["cajon"],
                    formato.hora(fila["entrada_en"]),
                    formato.duracion(fila["minutos"]),
                    self._acumulado(fila["minutos"], parametros),
                ),
                tags=tema.etiquetas_de_fila(indice),
            )

        self._pintar_estado_vacio(total=len(self._filas), visibles=len(filas))

        if patron:
            self.resumen_tablero.configure(
                text=f"{len(filas)} de {len(self._filas)} vehículo(s) coinciden con «{patron}»"
            )
        else:
            self.resumen_tablero.configure(
                text=f"{len(self._filas)} vehículo(s) dentro"
            )

    def _parametros_tarifa(self):
        """Parámetros de la tarifa vigente, o `None` si todavía no hay ninguna."""
        try:
            from apps.estacionamiento.domain.tarifas import ParametrosTarifa

            tarifa = services.obtener_tarifa_vigente()
            return ParametrosTarifa(
                cuota_hora=tarifa.cuota_hora,
                minutos_cortesia=tarifa.minutos_cortesia,
                fraccion_minutos=tarifa.fraccion_minutos,
                cobro_minimo=tarifa.cobro_minimo,
                tope_diario=tarifa.tope_diario,
            )
        except Exception:
            # Sin tarifa vigente la columna queda en blanco; el aviso de
            # configuración pendiente ya se dio al arrancar.
            return None

    def _acumulado(self, minutos: int, parametros) -> str:
        """
        Importe que llevaría acumulado el vehículo si saliera ahora.

        Es una referencia, no un cobro: el importe que se cobra sigue saliendo
        de `cotizar`, con la tarifa que estaba vigente al entrar —que puede no
        ser la de hoy—. Sirve para responder «¿cuánto llevo?» sin tener que
        cotizar folio por folio.
        """
        if parametros is None:
            return "—"
        try:
            from apps.estacionamiento.domain.tarifas import cotizar

            return formato.moneda(cotizar(minutos, parametros).importe)
        except Exception:
            return "—"

    def _pintar_estado_vacio(self, *, total: int, visibles: int) -> None:
        if visibles:
            self.vacio.grid_forget()
            return
        if total == 0:
            texto = "No hay vehículos dentro.\nRegistre una entrada para empezar."
        else:
            texto = "Ninguna placa coincide con la búsqueda."
        self.vacio.configure(text=texto)
        self.vacio.grid(row=1, column=0, sticky="nsew", pady=tema.ESPACIO_XL)

    def al_entrar(self) -> None:
        """Invocado al seleccionar la pestaña: deja el foco listo para teclear."""
        self.placa_entrada.focus_set()

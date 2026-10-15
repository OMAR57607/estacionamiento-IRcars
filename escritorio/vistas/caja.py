"""
Pestaña de operación: entrada, cálculo y cobro.

Es la pantalla donde el operador pasa el turno completo, por lo que su diseño
atiende el uso real, no la elegancia:

* **El trabajo está numerado.** «1 Entrada», «2 Salida y cobro», y dentro del
  cobro los tres movimientos en orden. Dos tarjetas con título sustantivo se
  leen como dos cosas que están ahí; numeradas se leen como una secuencia, y
  quien llega nuevo deja de preguntar por dónde empezar.

* **Teclado antes que ratón.** `F2` registra entrada, `F4` calcula el cobro y
  `F5` cobra. Con un cliente esperando, mover la mano al ratón cuesta segundos
  en cada vehículo.

* **Un solo sitio para la cifra que importa.** El visor grande muestra el
  importe a cobrar y, una vez cobrado, el **cambio a devolver**. Son los dos
  números que el operador dice en voz alta, nunca a la vez, así que comparten
  el único lugar de la pantalla donde la vista ya sabe buscar. Antes el cambio
  a devolver aterrizaba en una etiqueta estrecha al lado del campo de pago,
  que es donde menos se mira cuando ya se cerró el folio.

* **El cambio se calcula mientras se teclea.** El operador teclea lo que
  recibió y ve de inmediato cuánto devolver —o cuánto falta—, todavía a tiempo
  de corregir.

* **Calcular antes de cobrar.** Son dos pasos separados a propósito: el
  operador informa el monto, recibe el efectivo y solo entonces cierra. Un
  botón único que cobrara y cerrara de golpe convertiría cualquier error de
  captura en un folio cerrado e inmutable.

* **Lista de vehículos dentro, con búsqueda.** Permite localizar una placa
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
from apps.estacionamiento.domain.tarifas import ParametrosTarifa, cotizar

from .. import formato, tema
from ..errores import operacion

# Refresco de la lista. Treinta segundos es suficiente: los minutos que importan
# se recalculan al cobrar, y un refresco agresivo robaría respuesta a la
# captura de placas.
INTERVALO_REFRESCO_MS = 30_000

# Tiempo que permanece visible un aviso de operación cumplida. Lo bastante para
# leerlo, lo bastante poco para no confundirse con el del vehículo siguiente:
# un «cobrado $45.00» que sobrevive tres clientes es información falsa.
DURACION_AVISO_MS = 12_000

# Denominaciones de uso corriente. Evitan teclear el importe recibido en el
# caso más frecuente, que es que el cliente pague con un billete.
BILLETES = (50, 100, 200, 500)

TEXTO_INACTIVO = "Capture la placa y pulse «Calcular cobro»."


class VistaCaja(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=tema.ESPACIO_L, style="Fondo.TFrame")
        self.contexto = contexto
        self._folio_cotizado = None
        self._cotizacion = None
        self._filas = []
        self._aviso_entrada_id = None
        self._aviso_cobro_id = None
        self._refresco_id = None

        # Dos columnas con sentido propio: a la izquierda lo que entra y
        # quién está dentro; a la derecha lo que sale y se cobra. La lista
        # ocupa el alto sobrante de su columna en lugar de quedar aplastada
        # bajo dos tarjetas, que era el defecto del acomodo anterior.
        self.columnconfigure(0, weight=5, uniform="col")
        self.columnconfigure(1, weight=4, uniform="col")
        self.rowconfigure(1, weight=1)

        self._construir_entrada()
        self._construir_salida()
        self._construir_tablero()
        self.refrescar()

        # Un cambio de precio o de turno hecho en otra pestaña debe reflejarse
        # aquí: la columna del acumulado se calcula con el precio vigente.
        contexto.al_cambiar(self._al_cambiar_contexto)

    # ------------------------------------------------------------------ UI
    def _construir_entrada(self) -> None:
        tarjeta = tema.Tarjeta(self)
        tarjeta.grid(row=0, column=0, sticky="new", padx=(0, tema.ESPACIO_M))
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        tema.Paso(
            cuerpo,
            1,
            "Entra un vehículo",
            "Teclee la placa y pulse Enter.",
        ).grid(row=0, column=0, sticky="ew", pady=(0, tema.ESPACIO_M))

        self.placa_entrada = ttk.Entry(
            cuerpo, font=tema.fuente("captura"), style="Captura.TEntry"
        )
        self.placa_entrada.grid(row=1, column=0, sticky="ew")
        self.placa_entrada.bind("<Return>", lambda _e: self.registrar_entrada())

        ttk.Label(
            cuerpo,
            text="ABC-123, abc123 o abc 123 son la misma placa.",
            style="Tenue.TLabel",
            wraplength=300,
            justify="left",
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
            wraplength=300,
        )
        self.aviso_entrada.grid(row=4, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0))

    def _construir_salida(self) -> None:
        tarjeta = tema.Tarjeta(self)
        tarjeta.grid(row=0, column=1, rowspan=2, sticky="nsew")
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.columnconfigure(1, weight=1)

        tema.Paso(
            cuerpo,
            2,
            "Sale un vehículo",
            "Teclee la placa y pulse Enter.",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, tema.ESPACIO_M))

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
            text="Calcular cobro   ·   F4",
            style="Principal.TButton",
            command=self.cotizar,
        ).grid(row=0, column=1, padx=(tema.ESPACIO_S, 0))

        # --- Visor: importe a cobrar y, tras cobrar, cambio a devolver ------
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

        # La cifra existe en pantalla desde antes de haber calculado nada, en
        # gris y con un guion. Un hueco vacío no enseña dónde mirar; este sí.
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
            text=TEXTO_INACTIVO,
            background=tema.SUPERFICIE_ALT,
            foreground=tema.TEXTO_SEC,
            font=tema.fuente("cuerpo"),
            justify="left",
            anchor="w",
            wraplength=380,
        )
        self.detalle_cobro.grid(row=2, column=0, sticky="w", pady=(tema.ESPACIO_S, 0))

        # --- Pago -----------------------------------------------------------
        ttk.Label(
            cuerpo,
            text="¿Con cuánto paga?   ·   opcional: vacío si no recibe efectivo",
            style="Secundario.TLabel",
        ).grid(
            row=3, column=0, columnspan=2, sticky="w",
            pady=(tema.ESPACIO_M, tema.ESPACIO_XS),
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

        # Resultado del cálculo en vivo: cambio a devolver o faltante. Junto al
        # campo del pago, no debajo: es la cifra que el operador lee mientras
        # todavía tiene los billetes en la mano.
        self.aviso_cambio = tk.Label(
            cuerpo,
            text="",
            background=tema.SUPERFICIE,
            foreground=tema.EXITO,
            font=tema.fuente("cifra_chica"),
            anchor="w",
        )
        self.aviso_cambio.grid(row=4, column=1, sticky="w", padx=(tema.ESPACIO_M, 0))

        # Reparto a lo ancho en lugar de ancho natural: con cinco botones en
        # fila, el último se salía de la tarjeta.
        atajos = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        atajos.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(tema.ESPACIO_S, 0))
        ttk.Button(
            atajos,
            text="Justo",
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

        self.boton_cobrar = ttk.Button(
            cuerpo,
            text="Cobrar y cerrar   ·   F5",
            style="Principal.TButton",
            command=self.cobrar,
            state="disabled",
        )
        # Fila elástica entre los atajos y el botón: ancla la acción principal
        # al pie de la tarjeta, de modo que esté siempre en el mismo punto de
        # la pantalla sin importar cuántas líneas traiga el detalle del folio.
        cuerpo.rowconfigure(6, weight=1)

        # Por qué está apagado el botón. Un control deshabilitado sin motivo se
        # lee como un fallo del programa, y el operador llama a soporte. Va
        # encima del botón y no debajo: debajo queda contra el borde de la
        # tarjeta y es lo primero que desaparece cuando la pantalla aprieta.
        self.motivo_cobrar = ttk.Label(
            cuerpo,
            text="Primero calcule el cobro de una placa.",
            style="Tenue.TLabel",
            anchor="center",
        )
        self.motivo_cobrar.grid(
            row=7, column=0, columnspan=2, sticky="sew", pady=(tema.ESPACIO_S, 0)
        )
        self.boton_cobrar.grid(
            row=8, column=0, columnspan=2, sticky="sew", pady=(tema.ESPACIO_XS, 0)
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
        # Enter en el buscador calcula directamente el cobro de la única
        # coincidencia: es el camino completo «perdí el boleto» sin tocar el
        # ratón.
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
                ("entrada", "Entró a las", 110, "w"),
                ("permanencia", "Lleva dentro", 150, "w"),
                ("importe", "Debe ahora", 150, "e"),
            ),
            altura=8,
        )
        self.marco_tabla.grid(row=1, column=0, sticky="nsew")

        # Doble clic o Enter sobre una fila la llevan al cobro: es el gesto
        # para atender a quien perdió el boleto.
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

        self.resumen_tablero = ttk.Label(
            cuerpo,
            text="",
            style="Tenue.TLabel",
        )
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
            f"✓  {folio.placa} entró a las {formato.hora(folio.entrada_en)}{ubicacion}"
        )
        self.refrescar()
        self.contexto.notificar()

    @operacion("Cobro")
    def cotizar(self) -> None:
        """Calcula lo que debe pagar una placa, sin cerrar nada todavía."""
        # El acuse del cobro anterior se borra solo a los doce segundos. Si en
        # ese intervalo ya se calculó el siguiente, hay que cancelar el borrado:
        # de lo contrario limpiaría un importe recién calculado y el operador
        # vería desaparecer la cifra que acababa de pedir.
        self._cancelar_acuse()

        folio = services.buscar_folio_abierto_por_placa(self.placa_salida.get())
        if folio is None:
            messagebox.showwarning(
                "Salida",
                "No hay ningún vehículo dentro con esa placa.\n\n"
                "Revise la captura, o búsquela en la lista «Vehículos dentro» "
                "y haga doble clic sobre su renglón.",
                parent=self.winfo_toplevel(),
            )
            self._limpiar_cobro()
            return

        cotizacion = services.cotizar_folio(folio)
        self._folio_cotizado = folio
        self._cotizacion = cotizacion

        lineas = [
            f"{folio.placa}   ·   entró {formato.fecha_hora(folio.entrada_en)}",
            f"Lleva dentro {formato.duracion(cotizacion.minutos)}   ·   "
            f"a {formato.moneda(folio.tarifa.cuota_hora)} la hora",
        ]
        if cotizacion.es_cortesia:
            lineas.append(
                f"Está dentro de los {folio.tarifa.minutos_cortesia} minutos "
                "gratis: no se le cobra."
            )
        if cotizacion.tope_diario_aplicado:
            lineas.append("Se aplicó el máximo por día.")
        if cotizacion.cobro_minimo_aplicado:
            lineas.append("Se aplicó el cobro mínimo.")

        self.etiqueta_importe.configure(
            text="NO SE LE COBRA" if cotizacion.es_cortesia else "IMPORTE A COBRAR"
        )
        self.detalle_cobro.configure(text="\n".join(lineas))
        self.importe_grande.configure(
            text=formato.moneda(cotizacion.importe),
            foreground=tema.EXITO if cotizacion.es_cortesia else tema.TEXTO,
        )

        self.boton_cobrar.configure(state="normal")
        self.motivo_cobrar.configure(
            text=f"Al pulsar, el boleto de {folio.placa} se cierra. No se puede "
            "deshacer."
        )
        self.aviso_cambio.configure(text="")
        self.pago.delete(0, tk.END)
        self.pago.focus_set()

    @operacion("Cobro")
    def cobrar(self) -> None:
        if self._folio_cotizado is None:
            messagebox.showinfo(
                "Cobro",
                "Primero calcule el cobro: teclee la placa en «Sale un "
                "vehículo» y pulse Enter.",
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
                f"{exc}\n\nVaya a la pestaña «Turno y corte» y abra su turno. "
                "El vehículo sigue dentro y el boleto, abierto.",
                parent=self.winfo_toplevel(),
            )
            return

        self._limpiar_cobro()
        self._mostrar_resultado_cobro(folio, cotizacion)

        self.placa_salida.delete(0, tk.END)
        self.pago.delete(0, tk.END)
        self.placa_entrada.focus_set()
        self.refrescar()
        self.contexto.notificar()

    # ------------------------------------------------------------- Auxiliares
    def _mostrar_resultado_cobro(self, folio, cotizacion) -> None:
        """
        Deja el resultado del cobro en el visor grande.

        Si hay cambio que devolver, **ese** es el número protagonista: es lo
        que el operador tiene pendiente de hacer con el cliente delante. El
        importe cobrado pasa al renglón de detalle, donde sirve de acuse.
        """
        if cotizacion.es_cortesia:
            etiqueta, cifra, color = "SALIÓ SIN COBRO", formato.moneda(0), tema.EXITO
            detalle = f"✓  {folio.placa} salió dentro de los minutos gratis."
        elif folio.cambio is not None and folio.cambio > 0:
            etiqueta, cifra, color = (
                "CAMBIO A DEVOLVER",
                formato.moneda(folio.cambio),
                tema.EXITO,
            )
            detalle = (
                f"✓  {folio.placa}: cobrado {formato.moneda(folio.importe)} de "
                f"{formato.moneda(folio.pago_recibido)}."
            )
        else:
            etiqueta, cifra, color = (
                "COBRADO",
                formato.moneda(folio.importe),
                tema.EXITO,
            )
            detalle = f"✓  {folio.placa} pagó justo. No hay cambio que devolver."

        self.etiqueta_importe.configure(text=etiqueta, foreground=color)
        self.importe_grande.configure(text=cifra, foreground=color)
        self.detalle_cobro.configure(text=detalle, foreground=color)

        self._cancelar_acuse()
        self._aviso_cobro_id = self.after(DURACION_AVISO_MS, self._limpiar_cobro)

    def _cancelar_acuse(self) -> None:
        """Desactiva el borrado automático del acuse, si estaba programado."""
        if self._aviso_cobro_id is not None:
            self.after_cancel(self._aviso_cobro_id)
            self._aviso_cobro_id = None

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
                text="Paga justo · sin cambio", foreground=tema.EXITO
            )
        else:
            self.aviso_cambio.configure(
                text=f"Devolver {formato.moneda(diferencia)}",
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

    def _limpiar_cobro(self) -> None:
        # Llega tanto por el temporizador del acuse como por una llamada
        # directa; en el primer caso el identificador ya está consumido.
        self._aviso_cobro_id = None
        self._folio_cotizado = None
        self._cotizacion = None
        self.etiqueta_importe.configure(
            text="IMPORTE A COBRAR", foreground=tema.TEXTO_SEC
        )
        self.detalle_cobro.configure(text=TEXTO_INACTIVO, foreground=tema.TEXTO_SEC)
        self.importe_grande.configure(text="—", foreground=tema.DESHABILITADO)
        self.aviso_cambio.configure(text="")
        self.boton_cobrar.configure(state="disabled")
        self.motivo_cobrar.configure(text="Primero calcule el cobro de una placa.")

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

    def _al_cambiar_contexto(self) -> None:
        """
        Reacciona a un cambio hecho en otra pestaña (turno o precio).

        Solo repinta: volver a consultar el patio aquí duplicaría la consulta
        que el refresco periódico ya hace, y el dato que puede haber cambiado
        —el precio— solo afecta a la columna del acumulado.
        """
        self._pintar_filas()

    @operacion("Vehículos dentro")
    def refrescar(self) -> None:
        """
        Recarga la lista y reprograma el siguiente refresco.

        Se cancela el refresco pendiente antes de programar el siguiente. Sin
        eso, cada pulsación de F9 dejaba vivo un temporizador adicional y la
        pantalla acababa consultando la base varias veces por minuto sin que
        nadie lo hubiera pedido.
        """
        if self._refresco_id is not None:
            self.after_cancel(self._refresco_id)
            self._refresco_id = None

        try:
            self._filas = services.folios_en_patio()
            self._pintar_filas()
        finally:
            # Reprogramación encadenada en lugar de un hilo: Tkinter no es
            # seguro para accesos desde otros hilos, y `after` mantiene todo en
            # el hilo de la interfaz sin bloquearla.
            #
            # Va en `finally` porque el temporizador anterior ya se canceló
            # arriba: si una consulta fallida saliera por la vía de la
            # excepción, no quedaría ninguno vivo y la lista se congelaría el
            # resto de la sesión sin que nada lo advirtiera.
            self._refresco_id = self.after(INTERVALO_REFRESCO_MS, self.refrescar)

    def _pintar_filas(self) -> None:
        """
        Vuelca a la lista las filas que pasan el filtro.

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

        # El precio se resuelve una vez por repintado y no una vez por fila: con
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
                text=f"{len(filas)} de {len(self._filas)} vehículo(s) coinciden "
                f"con «{patron}»"
            )
        else:
            self.resumen_tablero.configure(
                text=f"{len(self._filas)} vehículo(s) dentro   ·   doble clic en "
                "un renglón para cobrarle"
            )

    def _parametros_tarifa(self):
        """Parámetros del precio vigente, o `None` si todavía no hay ninguno."""
        try:
            tarifa = services.obtener_tarifa_vigente()
            return ParametrosTarifa(
                cuota_hora=tarifa.cuota_hora,
                minutos_cortesia=tarifa.minutos_cortesia,
                fraccion_minutos=tarifa.fraccion_minutos,
                cobro_minimo=tarifa.cobro_minimo,
                tope_diario=tarifa.tope_diario,
            )
        except Exception:
            # Sin precio vigente la columna queda en blanco; la banda de guía
            # de la ventana ya reclama la configuración pendiente.
            return None

    def _acumulado(self, minutos: int, parametros) -> str:
        """
        Lo que debería pagar el vehículo si saliera ahora.

        Es una referencia, no un cobro: el importe que se cobra sigue saliendo
        de `cotizar`, con el precio que estaba vigente al entrar —que puede no
        ser el de hoy—. Sirve para responder «¿cuánto llevo?» sin tener que
        calcular boleto por boleto.
        """
        if parametros is None:
            return "—"
        try:
            resultado = cotizar(minutos, parametros)
            return "gratis" if resultado.es_cortesia else formato.moneda(resultado.importe)
        except Exception:
            return "—"

    def _pintar_estado_vacio(self, *, total: int, visibles: int) -> None:
        if visibles:
            self.vacio.grid_forget()
            self.marco_tabla.grid()
            return
        self.marco_tabla.grid_remove()
        if total == 0:
            texto = (
                "No hay ningún vehículo dentro.\n\n"
                "Registre una entrada con el campo de arriba a la izquierda."
            )
        else:
            texto = "Ninguna placa coincide con la búsqueda."
        self.vacio.configure(text=texto)
        self.vacio.grid(row=1, column=0, sticky="nsew", pady=tema.ESPACIO_XL)

    def al_entrar(self) -> None:
        """Invocado al seleccionar la pestaña: deja el foco listo para teclear."""
        self.placa_entrada.focus_set()

"""
Pestaña del precio.

El precio es un dato que se configura una vez y se deja quieto. Esta pantalla
está construida sobre esa idea, que antes no cumplía: era un formulario con
seis campos siempre abiertos y un botón de guardar, de modo que cada visita
invitaba a cambiar la tarifa, y los campos arrancaban con valores fijos de
ejemplo —no con los vigentes—, así que «subir un peso la hora» reescribía en
silencio la cortesía, la fracción y el tope.

Ahora el precio está **bloqueado**:

1. La pantalla muestra el precio vigente en **solo lectura**. No hay ningún
   campo que se pueda teclear por descuido.

2. Cambiarlo exige un acto deliberado: pulsar «Cambiar precio», confirmar la
   contraseña del operador administrativo y repasar un resumen del antes y el
   después. Tres pasos para una operación que se hace una vez al año, y
   ninguno para la que se hace cien veces al día.

3. El formulario del cambio parte **de los valores vigentes**, no de valores de
   ejemplo. Lo que no se toca, no cambia.

Se conserva el histórico: guardar crea un precio nuevo y desactiva el
anterior. Es lo que permite demostrar qué precio estaba exhibido en una fecha
determinada, y los folios ya cobrados mantienen el suyo.

La tabla de ejemplos («así se cobra») es la otra mitad del asunto. Los cinco
parámetros del cuadro tarifario no se entienden leyéndolos: «fracción de cobro
1 min» no le dice nada a nadie. Ver que una hora cuesta $30 y que media cuesta
$15 sí, y permite comprobar el precio sin hacer cuentas a mano.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from django.db import transaction
from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.domain.tarifas import ParametrosTarifa, cotizar
from apps.estacionamiento.models import Tarifa

from .. import formato, seguridad, tema
from ..errores import operacion

# Permanencias de muestra para la tabla de ejemplos. Cubren el rango real de
# una caseta: la maniobra, la gestión corta, la jornada y el día completo.
EJEMPLOS = (
    (15, "15 minutos"),
    (30, "Media hora"),
    (60, "1 hora"),
    (120, "2 horas"),
    (480, "8 horas"),
    (1440, "24 horas"),
)

# Definición del formulario de cambio: clave del modelo, etiqueta en lenguaje
# de caseta, y la explicación que va debajo del campo. Las etiquetas evitan el
# vocabulario contable: quien cambia el precio es el dueño, no un auditor.
CAMPOS = (
    (
        "cuota_hora",
        "Precio por hora",
        "Lo que cuesta una hora completa. Es la cifra del letrero.",
    ),
    (
        "minutos_cortesia",
        "Minutos gratis al entrar",
        "Tolerancia sin costo, para maniobrar o dejar a alguien. 0 = se cobra "
        "desde el primer minuto.",
    ),
    (
        "fraccion_minutos",
        "Se cobra en bloques de (minutos)",
        "1 = se paga el tiempo exacto. 15 = todo bloque de 15 minutos iniciado "
        "se cobra completo.",
    ),
    (
        "cobro_minimo",
        "Cobro mínimo",
        "Piso del importe, pasados los minutos gratis. 0 = sin mínimo.",
    ),
    (
        "tope_diario",
        "Máximo por día",
        "Más de esto no se cobra por cada día iniciado. Vacío = sin máximo.",
    ),
)


def _parametros(tarifa: Tarifa) -> ParametrosTarifa:
    return ParametrosTarifa(
        cuota_hora=tarifa.cuota_hora,
        minutos_cortesia=tarifa.minutos_cortesia,
        fraccion_minutos=tarifa.fraccion_minutos,
        cobro_minimo=tarifa.cobro_minimo,
        tope_diario=tarifa.tope_diario,
    )


class VistaTarifa(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=tema.ESPACIO_L, style="Fondo.TFrame")
        self.contexto = contexto
        self.solo_lectura = not getattr(contexto.operador, "is_staff", False)

        self.columnconfigure(0, weight=3, uniform="tar")
        self.columnconfigure(1, weight=2, uniform="tar")
        self.rowconfigure(1, weight=1)

        self._construir_vigente()
        self._construir_ejemplos()
        self._construir_historial()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_vigente(self) -> None:
        tarjeta = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        tarjeta.grid(row=0, column=0, sticky="new", padx=(0, tema.ESPACIO_M))
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        encabezado = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew")
        encabezado.columnconfigure(0, weight=1)

        # El candado va arriba y a la izquierda, antes que la cifra: lo primero
        # que debe quedar claro es que esto no se edita aquí.
        self.pildora_candado = tema.Pildora(encabezado, "")
        self.pildora_candado.grid(row=0, column=0, sticky="w")

        # La cifra que el cliente pregunta y que va en el letrero: en grande.
        self.cuota_vigente = ttk.Label(cuerpo, text="—", style="Display.TLabel")
        self.cuota_vigente.grid(row=1, column=0, sticky="w", pady=(tema.ESPACIO_M, 0))
        self.unidad_vigente = ttk.Label(cuerpo, text="por hora", style="Secundario.TLabel")
        # La vigencia va aquí, en pequeño, y no como un renglón más de la
        # ficha: es un dato de consulta, no un parámetro del precio.
        self.desde_vigente = ttk.Label(cuerpo, text="", style="Tenue.TLabel")
        self.unidad_vigente.grid(row=2, column=0, sticky="w")
        self.desde_vigente.grid(row=3, column=0, sticky="w")

        self.detalle_vigente = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        self.detalle_vigente.grid(
            row=5, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0)
        )
        self.detalle_vigente.columnconfigure(1, weight=1)

        self.boton_cambiar = ttk.Button(
            cuerpo,
            text="Cambiar precio…",
            style="Secundario.TButton",
            command=self.cambiar_precio,
        )
        self.boton_cambiar.grid(row=6, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0))

        # Explicación al pie del botón y no en un recuadro de color: la píldora
        # del candado ya da la señal, y el recuadro costaba el alto que
        # necesitaba la lista de cambios de abajo.
        self.nota_candado = ttk.Label(
            cuerpo,
            text="",
            style="Tenue.TLabel",
            wraplength=330,
            justify="left",
        )
        self.nota_candado.grid(row=7, column=0, sticky="ew", pady=(tema.ESPACIO_XS, 0))
        if self.solo_lectura:
            self.boton_cambiar.configure(state="disabled")

    def _construir_ejemplos(self) -> None:
        tarjeta = tema.Tarjeta(self, "Así se cobra", relleno=tema.ESPACIO_M)
        tarjeta.grid(row=0, column=1, sticky="new")
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(1, weight=1)

        ttk.Label(
            cuerpo,
            text="Lo que pagaría un vehículo según el tiempo que se quede, con "
            "el precio vigente.",
            style="Secundario.TLabel",
            wraplength=240,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, tema.ESPACIO_M))

        self.marco_ejemplos = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        self.marco_ejemplos.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.marco_ejemplos.columnconfigure(1, weight=1)

    def _construir_historial(self) -> None:
        tarjeta = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        tarjeta.grid(
            row=1, column=0, columnspan=2, sticky="nsew", pady=(tema.ESPACIO_M, 0)
        )
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(1, weight=1)

        encabezado = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew", pady=(0, tema.ESPACIO_S))
        encabezado.columnconfigure(0, weight=1)
        ttk.Label(encabezado, text="CAMBIOS DE PRECIO", style="Seccion.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            encabezado,
            text="Cada cambio queda registrado. Los cobros ya hechos conservan "
            "el precio con que se calcularon.",
            style="Tenue.TLabel",
        ).grid(row=0, column=1, sticky="e")

        marco, self.tabla = tema.tabla_con_barra(
            cuerpo,
            (
                ("vigencia", "Desde", 140, "w"),
                ("cuota", "Por hora", 95, "e"),
                ("cortesia", "Min. gratis", 95, "e"),
                ("fraccion", "Bloque", 85, "e"),
                ("minimo", "Mínimo", 95, "e"),
                ("tope", "Máx./día", 95, "e"),
                ("activa", "En uso", 75, "center"),
            ),
            altura=2,
        )
        self.marco_historial = marco
        self.marco_historial.grid(row=1, column=0, sticky="nsew")

        # Una tabla con encabezados y nada debajo no distingue «todavía no hay
        # precios» de «no cargó».
        self.historial_vacio = ttk.Label(
            cuerpo,
            text="Todavía no hay ningún precio registrado.",
            style="Tenue.TLabel",
            anchor="center",
        )

    # -------------------------------------------------------------- Acciones
    @operacion("Precio")
    def cambiar_precio(self) -> None:
        """
        Abre el cambio de precio, previo desbloqueo.

        El desbloqueo con contraseña no es un trámite: es lo que convierte un
        clic de más en una decisión. Cobrar de menos y quedarse la diferencia
        empieza por poder bajar el precio sin dejar rastro, y aquí queda el
        registro de quién lo cambió y cuándo.
        """
        if self.solo_lectura:
            messagebox.showinfo(
                "Precio",
                "Solo el personal administrativo puede cambiar el precio.\n\n"
                "El precio no se cambia desde una caja.",
                parent=self.winfo_toplevel(),
            )
            return

        try:
            vigente = services.obtener_tarifa_vigente()
        except Exception:
            vigente = None

        # Primera configuración: no hay nada que proteger todavía, y el
        # operador acaba de teclear su contraseña para entrar. Pedirla otra vez
        # sería un obstáculo sin control que respalde.
        if vigente is not None and not self._desbloquear():
            return

        dialogo = DialogoPrecio(self.winfo_toplevel(), vigente, self.contexto.operador)
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.refrescar()
            # Las demás pestañas leen el precio al entrar; la de caja lo usa en
            # el tablero, así que se le avisa para que no siga mostrando el
            # acumulado con el precio anterior.
            self.contexto.notificar()

    def _desbloquear(self) -> bool:
        return seguridad.confirmar_identidad(
            self.winfo_toplevel(),
            self.contexto.operador,
            titulo="Desbloquear el precio",
            explicacion="Confirme su contraseña para poder cambiar el precio. "
            "El cambio queda registrado a su nombre.",
            boton="Desbloquear",
        )

    # ------------------------------------------------------------- Refresco
    def refrescar(self) -> None:
        for hijo in self.detalle_vigente.winfo_children():
            hijo.destroy()
        for hijo in self.marco_ejemplos.winfo_children():
            hijo.destroy()

        try:
            vigente = services.obtener_tarifa_vigente()
        except Exception:
            vigente = None

        if vigente is None:
            self._pintar_sin_precio()
        else:
            self._pintar_precio(vigente)

        self._recargar_historial()

    def _pintar_sin_precio(self) -> None:
        """
        Estado de primera instalación: no hay precio y nada puede operar.

        Se dice lo que no funciona y lo que hay que hacer, en ese orden, y el
        botón cambia de texto: «Cambiar precio» no tiene sentido cuando no hay
        ninguno que cambiar.
        """
        self.pildora_candado.actualizar(
            "FALTA CONFIGURAR EL PRECIO",
            fondo=tema.PELIGRO_SUAVE,
            color=tema.PELIGRO,
        )
        self.cuota_vigente.configure(text="—", style="DisplayTenue.TLabel")
        self.unidad_vigente.configure(text="sin precio configurado")
        self.desde_vigente.configure(text="")
        self.nota_candado.configure(
            text="No se pueden registrar entradas ni cobrar salidas hasta "
            "configurar el precio. Es lo primero que hay que hacer.",
            foreground=tema.PELIGRO,
        )
        self.boton_cambiar.configure(text="Configurar el precio")
        if not self.solo_lectura:
            self.boton_cambiar.configure(style="Principal.TButton")

        ttk.Label(
            self.marco_ejemplos,
            text="Los ejemplos aparecen en cuanto haya un precio configurado.",
            style="Tenue.TLabel",
            wraplength=240,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

    def _pintar_precio(self, vigente: Tarifa) -> None:
        self.pildora_candado.actualizar(
            "PRECIO BLOQUEADO", fondo=tema.EXITO_SUAVE, color=tema.EXITO
        )
        self.cuota_vigente.configure(
            text=formato.moneda(vigente.cuota_hora), style="Display.TLabel"
        )
        self.unidad_vigente.configure(text="por hora")
        self.desde_vigente.configure(
            text=f"En uso desde el {formato.fecha_hora(vigente.vigente_desde)}"
        )
        self.boton_cambiar.configure(text="Cambiar precio…", style="Secundario.TButton")

        if self.solo_lectura:
            self.nota_candado.configure(
                text="Solo lo cambia el personal administrativo. Desde una caja "
                "se consulta, no se modifica.",
                foreground=tema.TEXTO_TENUE,
            )
        else:
            self.nota_candado.configure(
                text="Para cambiarlo hay que confirmar su contraseña. El cambio "
                "queda registrado a su nombre.",
                foreground=tema.TEXTO_TENUE,
            )

        tema.renglones_de_datos(
            self.detalle_vigente,
            (
                ("Minutos gratis al entrar", f"{vigente.minutos_cortesia} min"),
                ("Se cobra en bloques de", f"{vigente.fraccion_minutos} min"),
                ("Cobro mínimo", formato.moneda(vigente.cobro_minimo)),
                (
                    "Máximo por día",
                    formato.moneda(vigente.tope_diario)
                    if vigente.tope_diario is not None
                    else "sin máximo",
                ),
            ),
        )

        parametros = _parametros(vigente)
        for fila, (minutos, etiqueta) in enumerate(EJEMPLOS):
            resultado = cotizar(minutos, parametros)
            ttk.Label(
                self.marco_ejemplos, text=etiqueta, style="Secundario.TLabel"
            ).grid(row=fila, column=0, sticky="w", pady=3)
            ttk.Label(
                self.marco_ejemplos,
                text="gratis" if resultado.es_cortesia else formato.moneda(resultado.importe),
                font=tema.fuente("cuerpo_fuerte"),
                foreground=tema.EXITO if resultado.es_cortesia else tema.TEXTO,
            ).grid(row=fila, column=1, sticky="e", pady=3)

    def _recargar_historial(self) -> None:
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)

        tarifas = list(Tarifa.objects.order_by("-vigente_desde")[:100])
        if tarifas:
            self.historial_vacio.grid_remove()
            self.marco_historial.grid()
        else:
            self.marco_historial.grid_remove()
            self.historial_vacio.grid(
                row=1, column=0, sticky="nsew", pady=tema.ESPACIO_XL
            )

        for indice, tarifa in enumerate(tarifas):
            self.tabla.insert(
                "",
                "end",
                values=(
                    formato.fecha_hora(tarifa.vigente_desde),
                    formato.moneda(tarifa.cuota_hora),
                    f"{tarifa.minutos_cortesia} min",
                    f"{tarifa.fraccion_minutos} min",
                    formato.moneda(tarifa.cobro_minimo),
                    formato.moneda(tarifa.tope_diario)
                    if tarifa.tope_diario is not None
                    else "—",
                    "Sí" if tarifa.activa else "—",
                ),
                tags=tema.etiquetas_de_fila(indice),
            )

    def al_entrar(self) -> None:
        self.refrescar()


class DialogoPrecio(tk.Toplevel):
    """
    Formulario del cambio de precio, con el efecto a la vista.

    Dos decisiones gobiernan esta ventana:

    * **Parte de lo vigente.** Cada campo llega con el valor que está en uso.
      El formulario anterior arrancaba con valores de ejemplo, de modo que
      cambiar la cuota por hora restablecía en silencio los otros cuatro
      parámetros a lo que decía el código.

    * **Enseña el resultado mientras se teclea.** Al lado de los campos hay una
      columna que recalcula, con cada tecla, lo que costaría quedarse media
      hora, una hora o el día entero. Nadie puede prever de memoria el efecto
      de «bloques de 15 minutos» combinado con un cobro mínimo; verlo sí.
    """

    def __init__(self, padre, vigente, operador):
        super().__init__(padre)
        self.guardado = False
        self._vigente = vigente
        self._operador = operador

        self.title("Cambiar el precio" if vigente else "Configurar el precio")
        self.configure(background=tema.SUPERFICIE)
        self.resizable(False, False)
        self.transient(padre)
        self.grab_set()

        marco = ttk.Frame(self, style="Tarjeta.TFrame", padding=tema.ESPACIO_XL)
        marco.grid(sticky="nsew")
        marco.columnconfigure(0, weight=3, uniform="dlg")
        marco.columnconfigure(1, weight=2, uniform="dlg")

        ttk.Label(
            marco,
            text="Cambiar el precio" if vigente else "Configurar el precio",
            style="Titulo.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            marco,
            text=(
                "Los campos traen el precio que está en uso. Lo que no toque, "
                "no cambia."
                if vigente
                else "Este es el precio con el que operará la caseta. Podrá "
                "cambiarlo después, desbloqueando la pantalla."
            ),
            style="Secundario.TLabel",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(tema.ESPACIO_S, tema.ESPACIO_L))

        self._construir_campos(marco)
        self._construir_vista_previa(marco)

        # --- Pie ------------------------------------------------------------
        aviso = tema.Aviso(
            marco,
            "Al guardar, este precio entra en vigencia de inmediato y el "
            "anterior queda en el histórico. Los cobros ya hechos no se tocan.",
            tono="aviso",
            ancho_texto=560,
        )
        aviso.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(tema.ESPACIO_L, 0))

        botones = ttk.Frame(marco, style="Tarjeta.TFrame")
        botones.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(tema.ESPACIO_L, 0))
        botones.columnconfigure(0, weight=1)
        ttk.Button(
            botones, text="Cancelar", style="Secundario.TButton", command=self.destroy
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            botones,
            text="Guardar y poner en vigencia",
            style="Principal.TButton",
            command=self.guardar,
        ).grid(row=0, column=1, sticky="e")

        self.bind("<Escape>", lambda _e: self.destroy())
        self._refrescar_vista_previa()
        self._centrar(padre)
        self.campos["cuota_hora"].focus_force()
        self.campos["cuota_hora"].select_range(0, tk.END)

    # ------------------------------------------------------------------ UI
    def _construir_campos(self, marco) -> None:
        contenedor = ttk.Frame(marco, style="Tarjeta.TFrame")
        contenedor.grid(row=2, column=0, sticky="nw", padx=(0, tema.ESPACIO_XL))
        contenedor.columnconfigure(1, weight=1)

        iniciales = self._valores_iniciales()

        self.campos: dict[str, ttk.Entry] = {}
        fila = 0
        for clave, etiqueta, ayuda in CAMPOS:
            ttk.Label(contenedor, text=etiqueta, style="Secundario.TLabel").grid(
                row=fila, column=0, sticky="w", pady=(tema.ESPACIO_S, 0)
            )
            campo = ttk.Entry(contenedor, width=12, font=tema.fuente("captura"))
            campo.grid(
                row=fila,
                column=1,
                sticky="e",
                padx=(tema.ESPACIO_M, 0),
                pady=(tema.ESPACIO_S, 0),
            )
            campo.insert(0, iniciales[clave])
            # La vista previa se recalcula con cada tecla: es lo que convierte
            # cinco parámetros abstractos en una decisión informada.
            campo.bind("<KeyRelease>", lambda _e: self._refrescar_vista_previa())
            self.campos[clave] = campo

            ttk.Label(
                contenedor,
                text=ayuda,
                style="Tenue.TLabel",
                wraplength=300,
                justify="left",
            ).grid(row=fila + 1, column=0, columnspan=2, sticky="w")
            fila += 2

    def _construir_vista_previa(self, marco) -> None:
        contenedor = tk.Frame(
            marco,
            background=tema.SUPERFICIE_ALT,
            highlightbackground=tema.BORDE,
            highlightthickness=1,
            bd=0,
        )
        contenedor.grid(row=2, column=1, sticky="new")

        interior = tk.Frame(contenedor, background=tema.SUPERFICIE_ALT, padx=14, pady=12)
        interior.pack(fill="both", expand=True)
        interior.columnconfigure(1, weight=1)

        tk.Label(
            interior,
            text="CON ESTE PRECIO SE COBRARÍA",
            background=tema.SUPERFICIE_ALT,
            foreground=tema.TEXTO_SEC,
            font=tema.fuente("seccion"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, tema.ESPACIO_S))

        self._previas: dict[int, tk.Label] = {}
        for fila, (minutos, etiqueta) in enumerate(EJEMPLOS, start=1):
            tk.Label(
                interior,
                text=etiqueta,
                background=tema.SUPERFICIE_ALT,
                foreground=tema.TEXTO_SEC,
                font=tema.fuente("cuerpo"),
                anchor="w",
            ).grid(row=fila, column=0, sticky="w", pady=2)
            valor = tk.Label(
                interior,
                text="—",
                background=tema.SUPERFICIE_ALT,
                foreground=tema.TEXTO,
                font=tema.fuente("cuerpo_fuerte"),
                anchor="e",
            )
            valor.grid(row=fila, column=1, sticky="e", pady=2)
            self._previas[minutos] = valor

        self.error_previa = tk.Label(
            interior,
            text="",
            background=tema.SUPERFICIE_ALT,
            foreground=tema.PELIGRO,
            font=tema.fuente("cuerpo"),
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self.error_previa.grid(
            row=len(EJEMPLOS) + 1, column=0, columnspan=2, sticky="w",
            pady=(tema.ESPACIO_S, 0),
        )

    def _valores_iniciales(self) -> dict[str, str]:
        """
        Valores de partida: los vigentes si los hay, o un punto de arranque.

        Los valores de arranque solo se usan en la primera instalación, cuando
        todavía no existe ningún precio que respetar.
        """
        if self._vigente is None:
            return {
                "cuota_hora": "30.00",
                "minutos_cortesia": "15",
                "fraccion_minutos": "1",
                "cobro_minimo": "0.00",
                "tope_diario": "250.00",
            }
        return {
            "cuota_hora": f"{self._vigente.cuota_hora:.2f}",
            "minutos_cortesia": str(self._vigente.minutos_cortesia),
            "fraccion_minutos": str(self._vigente.fraccion_minutos),
            "cobro_minimo": f"{self._vigente.cobro_minimo:.2f}",
            "tope_diario": (
                f"{self._vigente.tope_diario:.2f}"
                if self._vigente.tope_diario is not None
                else ""
            ),
        }

    # ------------------------------------------------------------- Lectura
    def _leer(self) -> ParametrosTarifa:
        """
        Interpreta y valida la captura. Lanza `ValueError` con el motivo.

        La validación de rangos la hace el propio motor de tarifas al construir
        `ParametrosTarifa`: duplicarla aquí garantizaría que algún día una de
        las dos copias aceptara lo que la otra rechaza.
        """
        cuota = formato.a_decimal(
            self.campos["cuota_hora"].get(), campo="precio por hora"
        )
        minimo = formato.a_decimal(
            self.campos["cobro_minimo"].get() or "0", campo="cobro mínimo"
        )
        try:
            cortesia = int(self.campos["minutos_cortesia"].get().strip() or 0)
            fraccion = int(self.campos["fraccion_minutos"].get().strip() or 1)
        except ValueError as exc:
            raise ValueError(
                "Los minutos gratis y el bloque de cobro deben ser números "
                "enteros de minutos."
            ) from exc

        texto_tope = self.campos["tope_diario"].get().strip()
        tope = formato.a_decimal(texto_tope, campo="máximo por día") if texto_tope else None

        return ParametrosTarifa(
            cuota_hora=cuota,
            minutos_cortesia=cortesia,
            fraccion_minutos=fraccion,
            cobro_minimo=minimo,
            tope_diario=tope,
        )

    def _refrescar_vista_previa(self) -> None:
        """
        Recalcula los ejemplos con lo capturado.

        No se eleva ningún diálogo: aquí una captura a medias es normal, y un
        aviso por cada tecla haría la ventana inservible. El motivo se escribe
        al pie de la columna y los importes quedan en blanco.
        """
        try:
            parametros = self._leer()
        except Exception as exc:
            for valor in self._previas.values():
                valor.configure(text="—", foreground=tema.DESHABILITADO)
            self.error_previa.configure(text=str(exc))
            return

        self.error_previa.configure(text="")
        for minutos, valor in self._previas.items():
            resultado = cotizar(minutos, parametros)
            valor.configure(
                text="gratis" if resultado.es_cortesia else formato.moneda(resultado.importe),
                foreground=tema.EXITO if resultado.es_cortesia else tema.TEXTO,
            )

    # -------------------------------------------------------------- Guardar
    @operacion("Precio")
    def guardar(self) -> None:
        parametros = self._leer()

        # Resumen del antes y el después. Es la última oportunidad de notar que
        # se escribió 3.00 donde iba 30.00, y la cifra de una hora es la que
        # delata ese error de un vistazo.
        hora_nueva = cotizar(60, parametros)
        if self._vigente is None:
            detalle = (
                f"Precio por hora:   {formato.moneda(parametros.cuota_hora)}\n"
                f"Una hora costaría: {formato.moneda(hora_nueva.importe)}\n\n"
                "La caseta podrá operar en cuanto guarde."
            )
        else:
            hora_actual = cotizar(60, _parametros(self._vigente))
            detalle = (
                f"Por hora:   {formato.moneda(self._vigente.cuota_hora)}"
                f"   →   {formato.moneda(parametros.cuota_hora)}\n"
                f"Una hora:   {formato.moneda(hora_actual.importe)}"
                f"   →   {formato.moneda(hora_nueva.importe)}\n\n"
                "El precio anterior queda en el histórico y los cobros ya "
                "hechos no se tocan."
            )

        if not messagebox.askyesno(
            "Confirmar el precio",
            f"{detalle}\n\n¿Guarda este precio?",
            parent=self,
        ):
            return

        ahora = timezone.now()
        # Nombre interno con marca de tiempo: el modelo lo exige único y el
        # nombre comercial («General») se repetiría en cada cambio. Ya no se
        # pide al usuario, que no tiene por qué bautizar un precio.
        nombre = f"Precio {ahora:%Y-%m-%d %H:%M:%S}"

        # En una sola transacción: desactivar y crear son dos escrituras, y si
        # la segunda falla —el nombre lleva restricción única, así que dos
        # cambios dentro del mismo segundo colisionan— el estacionamiento se
        # quedaría sin ningún precio vigente y no podría ni registrar entradas
        # ni cobrar salidas hasta que alguien volviera a configurarlo.
        with transaction.atomic():
            Tarifa.objects.filter(activa=True).update(activa=False)
            Tarifa.objects.create(
                nombre=nombre,
                cuota_hora=parametros.cuota_hora,
                minutos_cortesia=parametros.minutos_cortesia,
                fraccion_minutos=parametros.fraccion_minutos,
                cobro_minimo=parametros.cobro_minimo,
                tope_diario=parametros.tope_diario,
                activa=True,
                vigente_desde=ahora,
            )

        self.guardado = True
        self.destroy()
        messagebox.showinfo(
            "Precio",
            f"El precio quedó en {formato.moneda(parametros.cuota_hora)} por hora "
            "y ya está en vigencia.\n\nVuelve a quedar bloqueado.",
            parent=self.master,
        )

    def _centrar(self, padre) -> None:
        self.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_rooty() + (padre.winfo_height() - self.winfo_height()) // 6
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

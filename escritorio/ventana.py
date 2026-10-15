"""
Ventana principal de la aplicación de caja.

Organiza las pestañas y mantiene el **contexto compartido**: el operador
autenticado y su turno abierto. Ese contexto es la única pieza de estado
global, y existe porque dos pestañas distintas —caja y turno— deben coincidir
en si hay turno abierto: si cada una lo consultara por su cuenta, podrían
discrepar y el operador vería información contradictoria.

La ventana aporta además las dos piezas que orientan al que llega nuevo:

* **La banda de guía**, bajo la cabecera y visible desde cualquier pestaña.
  Dice en una frase qué falta para poder cobrar —configurar el precio, abrir el
  turno— y trae el botón que lleva a hacerlo. Antes cada una de esas
  condiciones vivía encerrada en su propia pestaña, de modo que el operador las
  descubría al fallar el primer cobro, con el cliente ya en la ventanilla.

* **La ayuda (F1)**, que explica el circuito completo en lenguaje llano. Una
  caseta se atiende con rotación de personal; que el trabajo quepa en una
  pantalla de ayuda vale más que cualquier manual.

La cabecera **no muestra importe alguno del turno en curso**. Es la misma
restricción del cierre ciego que gobierna la pestaña de corte: si el total
cobrado estuviera a la vista, el conteo físico se ajustaría a él.
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
        """Avisa del cambio de turno o de precio a quien lo haya pedido."""
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
        self.contexto.al_cambiar(self._pintar_guia)
        self._pintar_guia()
        self._tictac()
        # Misma razón que en el diálogo de acceso: tras autenticarse, la
        # ventana principal debe quedar al frente y con el foco, no detrás de
        # lo que el operador tuviera abierto.
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self._encaminar_primer_arranque()

    # ------------------------------------------------------------------ UI
    def _construir(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._construir_cabecera()

        self.banda = tema.Banda(self)
        self.banda.grid(row=1, column=0, sticky="ew")
        tk.Frame(self, background=tema.BORDE, height=1).grid(
            row=1, column=0, sticky="sew"
        )

        contenedor = ttk.Frame(self, style="Fondo.TFrame")
        contenedor.grid(row=2, column=0, sticky="nsew", padx=tema.ESPACIO_L)
        contenedor.columnconfigure(0, weight=1)
        contenedor.rowconfigure(0, weight=1)

        self.pestanas = ttk.Notebook(contenedor)
        self.pestanas.grid(row=0, column=0, sticky="nsew")

        self.vista_caja = VistaCaja(self.pestanas, self.contexto)
        self.vista_turno = VistaTurno(self.pestanas, self.contexto)
        self.vista_reportes = VistaReportes(self.pestanas, self.contexto)
        self.vista_tarifa = VistaTarifa(self.pestanas, self.contexto)

        # Nombres por la tarea que se hace en cada pestaña, no por el concepto
        # contable que hay detrás. «Tarifa» obligaba a saber que el precio se
        # cambia ahí; «Precio» lo dice.
        self.pestanas.add(self.vista_caja, text="Entradas y cobros")
        self.pestanas.add(self.vista_turno, text="Turno y corte")
        self.pestanas.add(self.vista_reportes, text="Reportes")
        self.pestanas.add(self.vista_tarifa, text="Precio")
        self.pestanas.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)

        self._construir_barra_estado()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    def _construir_cabecera(self) -> None:
        """
        Franja superior con la identidad, el operador, el reloj y la ayuda.

        El estado del turno ya no vive aquí sino en la banda de guía: una
        píldora de estado dice qué pasa, pero no qué hacer, y lo segundo es lo
        que el operador necesita cuando no puede cobrar.
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

        ttk.Button(
            derecha,
            text="Ayuda  ·  F1",
            style="Secundario.TButton",
            command=self.mostrar_ayuda,
        ).grid(row=0, column=2)

        # Línea divisoria: separa la cabecera del área de trabajo sin recurrir
        # a un cambio de color que compita con las tarjetas.
        tk.Frame(self, background=tema.BORDE, height=1).grid(
            row=0, column=0, sticky="sew"
        )

    def _construir_barra_estado(self) -> None:
        barra = ttk.Frame(self, style="Barra.TFrame", padding=(tema.ESPACIO_L, 8))
        barra.grid(row=3, column=0, sticky="ew")
        barra.columnconfigure(1, weight=1)

        # Los atajos se recuerdan viéndolos. Tenerlos siempre a la vista es lo
        # que hace que el operador deje de usar el ratón.
        ttk.Label(
            barra,
            text="F2 Entrada     F4 Calcular cobro     F5 Cobrar     "
            "F9 Actualizar     F1 Ayuda",
            style="TenueFondo.TLabel",
        ).grid(row=0, column=0, sticky="w")

        # La carpeta de datos se muestra de forma permanente: es lo que el
        # responsable debe respaldar, y de nada sirve si no sabe dónde está. Se
        # muestra la carpeta y no el archivo —la ruta completa desbordaba la
        # barra— y un clic la copia al portapapeles, que es lo que se hace con
        # ella.
        import os

        carpeta = os.path.dirname(arranque.ruta_base_datos())
        # Se muestra abreviada: la ruta completa empujaba los atajos
        # fuera de la barra. Al portapapeles va siempre la ruta entera.
        visible = carpeta if len(carpeta) <= 46 else "…" + carpeta[-45:]
        self.etiqueta_datos = ttk.Label(
            barra,
            text=f"Datos: {visible}  (clic para copiar)",
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
    def _pintar_guia(self) -> None:
        """
        Escribe en la banda la única cosa que el operador tiene que hacer ahora.

        El orden de las comprobaciones es el de las dependencias reales: sin
        precio no se puede ni registrar una entrada, así que eso se pide antes
        que el turno. Anunciar dos faltas a la vez obligaría a elegir, y una de
        las dos elecciones no lleva a ninguna parte.
        """
        try:
            services.obtener_tarifa_vigente()
        except Exception:
            self.banda.actualizar(
                etiqueta="FALTA EL PRECIO",
                mensaje="Configure el precio por hora. Sin precio no se pueden "
                "registrar entradas ni cobrar salidas.",
                tono="peligro",
                accion="Ir a configurarlo",
                al_pulsar=lambda: self.pestanas.select(self.vista_tarifa),
            )
            return

        if self.contexto.turno is None:
            self.banda.actualizar(
                etiqueta="SIN TURNO",
                mensaje="Abra su turno de caja para poder cobrar. Puede "
                "registrar entradas, pero no cobrar salidas.",
                tono="aviso",
                accion="Abrir turno",
                al_pulsar=lambda: self.pestanas.select(self.vista_turno),
            )
            return

        # La cuenta de vehículos es un adorno informativo: si la consulta
        # falla, la banda debe seguir diciendo lo esencial —que se puede
        # cobrar— en lugar de dejar la cabecera a medio pintar.
        try:
            dentro = f"{len(services.folios_en_patio())} vehículo(s) dentro"
        except Exception:
            logger.exception("No se pudo contar los vehículos en el patio")
            dentro = "caja lista"

        self.banda.actualizar(
            etiqueta=f"TURNO {self.contexto.turno.pk} ABIERTO",
            mensaje=f"Todo listo para cobrar.   ·   {dentro}",
            tono="exito",
        )

    def _tictac(self) -> None:
        from django.utils import timezone

        try:
            self.reloj.configure(
                text=timezone.localtime().strftime("%d/%m/%Y  %H:%M")
            )
            # El estado se repinta con el reloj: así la cuenta de vehículos
            # dentro de la banda no se queda vieja mientras el operador trabaja
            # en otra pestaña.
            self._pintar_guia()
        except Exception:
            # Un fallo aquí no debe matar el bucle. Antes la reprogramación
            # venía después de repintar, de modo que una consulta fallida
            # —basta que otro proceso tenga bloqueado el archivo de SQLite—
            # dejaba el reloj y la banda congelados el resto de la sesión, y la
            # banda podía seguir anunciando un turno ya cortado.
            logger.exception("Fallo al repintar la cabecera")
        finally:
            self.after(INTERVALO_RELOJ_MS, self._tictac)

    def _registrar_atajos(self) -> None:
        """
        Atajos globales de la caja.

        Se registran en la ventana, no en cada campo: el operador debe poder
        pulsar F5 sin importar dónde esté el cursor.
        """
        self.bind("<F1>", lambda _e: self.mostrar_ayuda())
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

    # ---------------------------------------------------------------- Ayuda
    def mostrar_ayuda(self) -> None:
        DialogoAyuda(self)

    # ------------------------------------------------------------ Validaciones
    def _encaminar_primer_arranque(self) -> None:
        """
        Deja abierta la pestaña que hay que atender en el primer arranque.

        Antes esto era un diálogo de advertencia. Se quitó: la banda de guía ya
        dice lo mismo de forma permanente y con el botón para resolverlo, y un
        aviso modal que hay que descartar antes de ver la aplicación es un
        obstáculo, no una ayuda.
        """
        try:
            services.obtener_tarifa_vigente()
        except Exception:
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


class DialogoAyuda(tk.Toplevel):
    """
    El trabajo de la caseta en una pantalla.

    Escrita como una secuencia de tareas y no como un catálogo de funciones: el
    operador no busca «qué hace el botón Cotizar», busca «cómo cobro». Por eso
    los títulos son las tareas del día y los atajos van al final, donde se
    consultan cuando ya se sabe qué se quiere hacer.
    """

    SECCIONES = (
        (
            "Al empezar el día",
            "1.  Abra su turno en «Turno y corte» y declare el cambio con que "
            "inicia la caja.\n"
            "2.  Sin turno abierto se pueden registrar entradas, pero no cobrar "
            "salidas.",
        ),
        (
            "Cuando entra un vehículo",
            "1.  Teclee la placa en «Entra un vehículo» y pulse Enter (o F2).\n"
            "2.  El vehículo aparece en la lista de los que están dentro.",
        ),
        (
            "Cuando sale un vehículo",
            "1.  Teclee la placa en «Sale un vehículo» y pulse Enter (o F4). Verá "
            "el importe en grande.\n"
            "2.  Dígaselo al cliente y teclee lo que le entregó: el cambio a "
            "devolver se calcula al instante.\n"
            "3.  Pulse «Cobrar y cerrar» (o F5).\n\n"
            "Si el cliente perdió el boleto, búsquelo por placa en la lista de "
            "abajo y haga doble clic sobre su renglón.",
        ),
        (
            "Al terminar el día",
            "1.  Cuente el efectivo que hay en caja, incluido el cambio inicial.\n"
            "2.  Captúrelo en «Turno y corte» y pulse «Cerrar turno y cortar».\n"
            "3.  La diferencia se revela después del conteo, nunca antes: es lo "
            "que hace válido el corte.",
        ),
        (
            "El precio",
            "El precio está bloqueado a propósito: se configura una vez y no se "
            "toca en la operación diaria.\n"
            "Para cambiarlo hace falta perfil administrativo y confirmar la "
            "contraseña. Cada cambio queda registrado.",
        ),
        (
            "Atajos de teclado",
            "F1  Esta ayuda\n"
            "F2  Registrar entrada\n"
            "F4  Calcular el cobro de una salida\n"
            "F5  Cobrar y cerrar el boleto\n"
            "F9  Actualizar la lista de vehículos dentro",
        ),
    )

    def __init__(self, padre):
        super().__init__(padre)
        self.title("IRcars — Ayuda")
        self.configure(background=tema.SUPERFICIE)
        self.transient(padre)
        self.grab_set()
        self.resizable(False, False)

        marco = ttk.Frame(self, style="Tarjeta.TFrame", padding=tema.ESPACIO_XL)
        marco.grid(sticky="nsew")
        marco.columnconfigure(0, weight=1, uniform="ayuda")
        marco.columnconfigure(1, weight=1, uniform="ayuda")

        ttk.Label(marco, text="Cómo se usa la caja", style="Titulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, tema.ESPACIO_L)
        )

        # Dos columnas: en una sola, la ayuda completa no cabe en pantalla y
        # obliga a desplazarse, que es cuando se deja de leer.
        for indice, (titulo, cuerpo) in enumerate(self.SECCIONES):
            columna, fila = divmod(indice, 3)
            bloque = ttk.Frame(marco, style="Tarjeta.TFrame")
            bloque.grid(
                row=fila + 1,
                column=columna,
                sticky="new",
                padx=(0, tema.ESPACIO_XL) if columna == 0 else 0,
                pady=(0, tema.ESPACIO_L),
            )
            bloque.columnconfigure(0, weight=1)
            ttk.Label(bloque, text=titulo.upper(), style="Seccion.TLabel").grid(
                row=0, column=0, sticky="w", pady=(0, tema.ESPACIO_XS)
            )
            ttk.Label(
                bloque,
                text=cuerpo,
                style="Secundario.TLabel",
                justify="left",
                wraplength=380,
            ).grid(row=1, column=0, sticky="w")

        ttk.Button(
            marco, text="Entendido", style="Principal.TButton", command=self.destroy
        ).grid(row=4, column=1, sticky="e", pady=(tema.ESPACIO_M, 0))

        self.bind("<Return>", lambda _e: self.destroy())
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_rooty() + (padre.winfo_height() - self.winfo_height()) // 4
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.focus_force()

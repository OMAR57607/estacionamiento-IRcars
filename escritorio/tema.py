"""
Sistema de diseño de la aplicación de caja.

Concentra color, tipografía y estilos de control en un solo lugar. La razón no
es estética sino operativa: una caseta se atiende de pie, con el cliente
esperando y a veces con luz de sol sobre la pantalla. Lo que el operador
necesita —el importe a cobrar, si hay turno abierto, el cambio a devolver—
tiene que leerse de un vistazo y sin confundirse con lo demás.

De ahí las tres reglas que rigen este archivo:

1. **Una sola acción principal por pantalla.** El botón que el operador pulsa
   decenas de veces al día se ve distinto de todos los demás. Si todo pesa
   igual, nada guía.

2. **El color significa, no decora.** Verde es cobrado, rojo es faltante o
   acción irreversible, ámbar es sobrante. Ningún color se usa por adorno, y
   ningún estado se comunica *solo* con color: siempre hay texto o signo que
   lo acompañe, porque un operador puede no distinguirlos.

3. **Jerarquía por tamaño antes que por adorno.** El dato que se dice en voz
   alta se muestra grande. No hay sombras ni degradados que Tk no pueda
   dibujar con nitidez.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# --------------------------------------------------------------------- Color
#
# Paleta de superficie neutra y un azul de marca. Los tonos «suaves» son
# fondos para avisos; los «fuertes», el texto o el borde que va encima, en
# pares ya verificados para que el contraste sea legible.

FONDO = "#eef1f5"          # Lienzo de la ventana
SUPERFICIE = "#ffffff"     # Tarjetas y tablas
SUPERFICIE_ALT = "#f7f9fb"  # Filas alternas, encabezados suaves
BORDE = "#d7dee7"
BORDE_FUERTE = "#b9c4d1"

TEXTO = "#16202b"          # Texto principal
TEXTO_SEC = "#55697c"      # Etiquetas, descripciones
TEXTO_TENUE = "#8496a8"    # Pies, metadatos

MARCA = "#1b5fd9"          # Azul de acción principal
MARCA_HOVER = "#1750b8"
MARCA_ACTIVA = "#124399"
MARCA_SUAVE = "#e7effc"

EXITO = "#15803d"
EXITO_SUAVE = "#e6f4ea"
PELIGRO = "#b42318"
PELIGRO_HOVER = "#991d14"
PELIGRO_SUAVE = "#fdecea"
AVISO = "#9a6200"
AVISO_SUAVE = "#fdf3e0"

DESHABILITADO = "#c6cfda"
TEXTO_DESHABILITADO = "#93a2b2"

# ----------------------------------------------------------------- Tipografía

# Orden de preferencia. Segoe UI es la de Windows, donde opera la caseta; las
# demás son alternativas razonables si el equipo no la tiene.
_FAMILIAS = ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", "Helvetica")

_familia_resuelta: str | None = None


def familia() -> str:
    """Primera tipografía disponible de la lista de preferencia."""
    global _familia_resuelta
    if _familia_resuelta is None:
        try:
            disponibles = set(tkfont.families())
        except Exception:
            disponibles = set()
        _familia_resuelta = next(
            (f for f in _FAMILIAS if f in disponibles), "TkDefaultFont"
        )
    return _familia_resuelta


# Escala tipográfica. Los nombres describen el papel del texto, no su tamaño:
# así un ajuste de tamaño se hace aquí y no persiguiendo números por el código.
# Los tamaños son puntos, que Tk amplía además por el factor de escala del
# sistema (1.33 en una pantalla al 125 %). De ahí que cifras aparentemente
# modestas se lean grandes en el equipo de la caseta.
_ESCALA = {
    "display": (31, "bold"),    # El importe a cobrar
    "cifra": (19, "bold"),      # Métricas de tarjeta
    "cifra_chica": (13, "bold"),
    "titulo": (14, "bold"),     # Título de pantalla
    "seccion": (9, "bold"),     # Encabezado de tarjeta
    "captura": (14, "normal"),  # Campos donde se teclea placa o importe
    "cuerpo": (9, "normal"),
    "cuerpo_fuerte": (9, "bold"),
    "pie": (8, "normal"),
}


def fuente(rol: str) -> tuple:
    """Fuente por papel semántico: `fuente("display")`, `fuente("cuerpo")`."""
    tamano, peso = _ESCALA[rol]
    return (familia(), tamano, peso)


# ------------------------------------------------------------------ Espaciado

ESPACIO_XS = 4
ESPACIO_S = 8
ESPACIO_M = 12
ESPACIO_L = 16
ESPACIO_XL = 24


def aplicar(raiz: tk.Misc) -> ttk.Style:
    """
    Registra todos los estilos. Se invoca una vez, al crear la ventana.

    Se parte del tema `clam` porque es el único de los incorporados que
    respeta los colores de fondo que se le piden en Windows: los temas
    nativos ignoran `background` en botones y encabezados, y la pantalla
    quedaría a medio pintar.
    """
    estilo = ttk.Style(raiz)
    if "clam" in estilo.theme_names():
        estilo.theme_use("clam")

    raiz.configure(background=FONDO)
    # La fuente por omisión alcanza a los widgets Tk clásicos (menús, diálogos
    # de mensaje), que no leen estilos de ttk.
    try:
        tkfont.nametofont("TkDefaultFont").configure(
            family=familia(), size=_ESCALA["cuerpo"][0]
        )
    except Exception:
        pass

    # --- Contenedores -----------------------------------------------------
    # Por omisión todo vive sobre una tarjeta blanca, que es el caso común;
    # «Fondo.» es la excepción para lo que se apoya en el lienzo.
    estilo.configure("TFrame", background=SUPERFICIE)
    estilo.configure("Fondo.TFrame", background=FONDO)
    estilo.configure("Tarjeta.TFrame", background=SUPERFICIE)
    estilo.configure("Cabecera.TFrame", background=SUPERFICIE)
    estilo.configure("Barra.TFrame", background=FONDO)

    # --- Texto ------------------------------------------------------------
    estilo.configure(
        "TLabel", background=SUPERFICIE, foreground=TEXTO, font=fuente("cuerpo")
    )
    estilo.configure("Fondo.TLabel", background=FONDO, foreground=TEXTO)
    estilo.configure(
        "Titulo.TLabel", background=SUPERFICIE, foreground=TEXTO, font=fuente("titulo")
    )
    estilo.configure(
        "Seccion.TLabel",
        background=SUPERFICIE,
        foreground=TEXTO_SEC,
        font=fuente("seccion"),
    )
    estilo.configure(
        "Secundario.TLabel", background=SUPERFICIE, foreground=TEXTO_SEC
    )
    estilo.configure(
        "Tenue.TLabel",
        background=SUPERFICIE,
        foreground=TEXTO_TENUE,
        font=fuente("pie"),
    )
    estilo.configure(
        "TenueFondo.TLabel",
        background=FONDO,
        foreground=TEXTO_TENUE,
        font=fuente("pie"),
    )
    estilo.configure(
        "Display.TLabel",
        background=SUPERFICIE,
        foreground=TEXTO,
        font=fuente("display"),
    )
    estilo.configure(
        "DisplayTenue.TLabel",
        background=SUPERFICIE,
        foreground=DESHABILITADO,
        font=fuente("display"),
    )
    estilo.configure(
        "Cifra.TLabel", background=SUPERFICIE, foreground=TEXTO, font=fuente("cifra")
    )
    estilo.configure(
        "Exito.TLabel",
        background=SUPERFICIE,
        foreground=EXITO,
        font=fuente("cuerpo_fuerte"),
    )
    estilo.configure(
        "Peligro.TLabel",
        background=SUPERFICIE,
        foreground=PELIGRO,
        font=fuente("cuerpo_fuerte"),
    )
    estilo.configure(
        "Aviso.TLabel",
        background=SUPERFICIE,
        foreground=AVISO,
        font=fuente("cuerpo_fuerte"),
    )

    # --- Botones ----------------------------------------------------------
    #
    # Tres pesos y nada más. «Principal» es la acción que el operador repite
    # todo el día; «Secundario» acompaña; «Peligro» marca lo irreversible —el
    # corte de caja— para que no se pulse por inercia.
    base_boton = dict(
        borderwidth=0,
        focusthickness=0,
        focuscolor="",
        relief="flat",
        anchor="center",
    )
    estilo.configure(
        "Principal.TButton",
        **base_boton,
        background=MARCA,
        foreground="#ffffff",
        font=fuente("cuerpo_fuerte"),
        padding=(16, 11),
    )
    estilo.map(
        "Principal.TButton",
        background=[
            ("disabled", DESHABILITADO),
            ("pressed", MARCA_ACTIVA),
            ("active", MARCA_HOVER),
        ],
        foreground=[("disabled", TEXTO_DESHABILITADO)],
    )

    estilo.configure(
        "Secundario.TButton",
        **base_boton,
        background=SUPERFICIE_ALT,
        foreground=TEXTO,
        font=fuente("cuerpo"),
        padding=(14, 9),
    )
    estilo.map(
        "Secundario.TButton",
        background=[
            ("disabled", SUPERFICIE_ALT),
            ("pressed", BORDE),
            ("active", "#e9eef4"),
        ],
        foreground=[("disabled", TEXTO_DESHABILITADO)],
    )

    estilo.configure(
        "Peligro.TButton",
        **base_boton,
        background=PELIGRO,
        foreground="#ffffff",
        font=fuente("cuerpo_fuerte"),
        padding=(16, 11),
    )
    estilo.map(
        "Peligro.TButton",
        background=[
            ("disabled", DESHABILITADO),
            ("pressed", PELIGRO_HOVER),
            ("active", PELIGRO_HOVER),
        ],
        foreground=[("disabled", TEXTO_DESHABILITADO)],
    )

    # Variante estrecha, para filas de varios botones donde el ancho escasea.
    estilo.configure(
        "Compacto.TButton",
        **base_boton,
        background=SUPERFICIE_ALT,
        foreground=TEXTO,
        font=fuente("cuerpo"),
        padding=(6, 8),
    )
    estilo.map(
        "Compacto.TButton",
        background=[
            ("disabled", SUPERFICIE_ALT),
            ("pressed", BORDE),
            ("active", "#e9eef4"),
        ],
        foreground=[("disabled", TEXTO_DESHABILITADO)],
    )

    # Botón de texto, para acciones terciarias (exportar, guardar comprobante).
    estilo.configure(
        "Enlace.TButton",
        **base_boton,
        background=SUPERFICIE,
        foreground=MARCA,
        font=fuente("cuerpo_fuerte"),
        padding=(8, 6),
    )
    estilo.map(
        "Enlace.TButton",
        background=[("active", MARCA_SUAVE), ("pressed", MARCA_SUAVE)],
        foreground=[("disabled", TEXTO_DESHABILITADO)],
    )

    # --- Campos de captura ------------------------------------------------
    for nombre in ("TEntry", "Captura.TEntry"):
        estilo.configure(
            nombre,
            fieldbackground=SUPERFICIE,
            background=SUPERFICIE,
            foreground=TEXTO,
            bordercolor=BORDE_FUERTE,
            lightcolor=BORDE_FUERTE,
            darkcolor=BORDE_FUERTE,
            insertcolor=TEXTO,
            borderwidth=1,
            relief="solid",
            padding=(10, 8),
        )
        # El foco se marca con el azul de marca: el operador debe ver sin
        # dudar en qué campo está escribiendo.
        estilo.map(
            nombre,
            bordercolor=[("focus", MARCA)],
            lightcolor=[("focus", MARCA)],
            darkcolor=[("focus", MARCA)],
            fieldbackground=[("disabled", SUPERFICIE_ALT)],
            foreground=[("disabled", TEXTO_DESHABILITADO)],
        )

    # --- Pestañas ---------------------------------------------------------
    #
    # Planas y sin marco. El biselado de `clam` dibuja cada pestaña como una
    # caja en relieve, y cuatro cajas en relieve compiten por la atención con
    # el contenido que importa.
    estilo.configure("TNotebook", background=FONDO, borderwidth=0, tabmargins=(0, 0, 0, 0))
    estilo.configure(
        "TNotebook.Tab",
        background=FONDO,
        foreground=TEXTO_SEC,
        font=fuente("cuerpo_fuerte"),
        padding=(20, 10),
        borderwidth=0,
        bordercolor=FONDO,
        lightcolor=FONDO,
        darkcolor=FONDO,
    )
    estilo.map(
        "TNotebook.Tab",
        background=[("selected", SUPERFICIE), ("active", "#e3e9f1")],
        foreground=[("selected", MARCA)],
        bordercolor=[("selected", SUPERFICIE)],
        lightcolor=[("selected", SUPERFICIE)],
        darkcolor=[("selected", SUPERFICIE)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    # --- Tablas -----------------------------------------------------------
    #
    # Filas altas: la lista se consulta de pie y a distancia de brazo, y un
    # renglón apretado se lee mal con prisa.
    estilo.configure(
        "Treeview",
        background=SUPERFICIE,
        fieldbackground=SUPERFICIE,
        foreground=TEXTO,
        rowheight=29,
        borderwidth=0,
        font=fuente("cuerpo"),
    )
    estilo.configure(
        "Treeview.Heading",
        background=SUPERFICIE_ALT,
        foreground=TEXTO_SEC,
        font=fuente("seccion"),
        padding=(10, 9),
        borderwidth=0,
        relief="flat",
    )
    estilo.map(
        "Treeview",
        background=[("selected", MARCA_SUAVE)],
        foreground=[("selected", TEXTO)],
    )
    estilo.map("Treeview.Heading", background=[("active", "#eaeff5")])
    estilo.layout(
        # Sin el marco del tema: la tarjeta que la contiene ya aporta el borde.
        "Treeview",
        [("Treeview.treearea", {"sticky": "nswe"})],
    )

    # --- Barras de desplazamiento ----------------------------------------
    estilo.configure(
        "Vertical.TScrollbar",
        background=BORDE,
        troughcolor=SUPERFICIE_ALT,
        bordercolor=SUPERFICIE_ALT,
        arrowcolor=TEXTO_SEC,
        borderwidth=0,
        relief="flat",
    )
    estilo.map("Vertical.TScrollbar", background=[("active", BORDE_FUERTE)])

    estilo.configure("TSeparator", background=BORDE)

    return estilo


# --------------------------------------------------------------- Componentes


class Tarjeta(ttk.Frame):
    """
    Superficie blanca con borde, título opcional y relleno uniforme.

    Sustituye al `LabelFrame` del tema por omisión, cuyo marco grabado y
    título incrustado en el borde producen una pantalla ruidosa cuando hay
    cuatro o cinco agrupaciones a la vista.

    El contenido se añade a `.cuerpo`, no a la tarjeta misma.
    """

    def __init__(self, padre, titulo: str | None = None, *, relleno: int = ESPACIO_L):
        # El borde se dibuja con `highlight*` de Tk y no con `relief`, que en
        # clam produce un biselado de aspecto anticuado.
        self._contenedor = tk.Frame(
            padre,
            background=SUPERFICIE,
            highlightbackground=BORDE,
            highlightcolor=BORDE,
            highlightthickness=1,
            bd=0,
        )
        super().__init__(self._contenedor, style="Tarjeta.TFrame", padding=relleno)
        super().pack(fill="both", expand=True)

        self.columnconfigure(0, weight=1)
        fila = 0
        if titulo:
            ttk.Label(self, text=titulo.upper(), style="Seccion.TLabel").grid(
                row=0, column=0, sticky="w", pady=(0, ESPACIO_M)
            )
            fila = 1

        self.cuerpo = ttk.Frame(self, style="Tarjeta.TFrame")
        self.cuerpo.grid(row=fila, column=0, sticky="nsew")
        self.rowconfigure(fila, weight=1)

    # La tarjeta se coloca en la ventana a través de su contenedor externo,
    # así que los gestores de geometría se redirigen a él.
    def grid(self, **kwargs):
        self._contenedor.grid(**kwargs)
        return self

    def grid_forget(self):
        self._contenedor.grid_forget()
        return self

    def pack(self, **kwargs):
        self._contenedor.pack(**kwargs)
        return self


class Metrica(ttk.Frame):
    """
    Una cifra con su etiqueta debajo.

    Un total de caja leído en un renglón de texto corrido se confunde con el
    de al lado. Separarlo en bloques, con la cifra grande y la etiqueta
    pequeña, permite localizar el dato sin leer la frase completa.
    """

    def __init__(self, padre, etiqueta: str, valor: str = "—", *, color: str = TEXTO):
        super().__init__(padre, style="Tarjeta.TFrame")
        self.valor = ttk.Label(self, text=valor, style="Cifra.TLabel", foreground=color)
        self.valor.grid(row=0, column=0, sticky="w")
        ttk.Label(self, text=etiqueta, style="Tenue.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )

    def actualizar(self, valor: str, *, color: str = TEXTO) -> None:
        self.valor.configure(text=valor, foreground=color)


class Pildora(tk.Label):
    """
    Indicador de estado compacto: «TURNO ABIERTO», «SIN TURNO».

    Lleva siempre texto, nunca solo color: el estado debe entenderse aunque
    el operador no distinga el verde del rojo.
    """

    def __init__(self, padre, texto: str = "", *, fondo: str = SUPERFICIE_ALT, color: str = TEXTO_SEC):
        super().__init__(
            padre,
            text=texto,
            background=fondo,
            foreground=color,
            font=(familia(), 9, "bold"),
            padx=10,
            pady=5,
            bd=0,
        )

    def actualizar(self, texto: str, *, fondo: str, color: str) -> None:
        self.configure(text=texto, background=fondo, foreground=color)


def tabla_con_barra(padre, columnas, *, altura: int = 10):
    """
    Treeview con barra de desplazamiento y filas alternas ya configuradas.

    Las cuatro pantallas muestran listas; sin esta función cada una repetiría
    la misma veintena de líneas y acabarían divergiendo en detalles.

    `columnas` es una secuencia de `(clave, título, ancho, anclaje)`.
    """
    marco = ttk.Frame(padre, style="Tarjeta.TFrame")
    marco.columnconfigure(0, weight=1)
    marco.rowconfigure(0, weight=1)

    tabla = ttk.Treeview(
        marco,
        columns=[c[0] for c in columnas],
        show="headings",
        selectmode="browse",
        height=altura,
    )
    for clave, titulo, ancho, anclaje in columnas:
        tabla.heading(clave, text=titulo, anchor=anclaje)
        tabla.column(clave, width=ancho, anchor=anclaje, stretch=True)
    tabla.grid(row=0, column=0, sticky="nsew")

    # Bandeado: a partir de la cuarta o quinta fila, seguir un renglón hasta
    # la columna del importe sin equivocarse de línea depende de esto.
    tabla.tag_configure("par", background=SUPERFICIE)
    tabla.tag_configure("impar", background=SUPERFICIE_ALT)
    tabla.tag_configure("faltante", foreground=PELIGRO)
    tabla.tag_configure("sobrante", foreground=AVISO)

    # La barra se muestra sola cuando hace falta: una barra de desplazamiento
    # permanente sobre una lista de cinco filas sugiere que hay más que ver.
    barra = ttk.Scrollbar(marco, orient="vertical", command=tabla.yview)

    def _sincronizar(inicio: str, fin: str) -> None:
        barra.set(inicio, fin)
        if float(inicio) <= 0.0 and float(fin) >= 1.0:
            barra.grid_remove()
        else:
            barra.grid(row=0, column=1, sticky="ns")

    tabla.configure(yscrollcommand=_sincronizar)

    return marco, tabla


def etiquetas_de_fila(indice: int, *extra: str) -> tuple[str, ...]:
    """Alternancia de color para `tabla_con_barra`, más etiquetas propias."""
    return (("par" if indice % 2 == 0 else "impar"),) + tuple(e for e in extra if e)

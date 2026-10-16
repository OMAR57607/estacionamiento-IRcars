"""
Ventana que muestra al cliente el código QR de su boleto.

Aparece justo después de registrar una entrada, que es el único momento en que
el cliente está frente a la caseta y puede escanear. Pedirle que lo haga más
tarde equivale a que no lo haga.

Tres decisiones de uso que conviene no revertir sin motivo:

* **Se cierra con cualquier tecla.** El operador tiene las manos en el teclado
  y un cliente esperando. Obligarlo a buscar un botón con el ratón añadiría
  segundos a cada vehículo, que es exactamente lo que el resto de esta
  interfaz evita.

* **La dirección se muestra también como texto.** Si la cámara del cliente no
  lee el símbolo —pantalla sucia, teléfono viejo, poca luz—, el operador puede
  dictarla. Sin esa salida, el fallo del QR deja al cliente sin alternativa.

* **No bloquea la operación.** Si falta la biblioteca de códigos QR o la
  publicación no está configurada, esta ventana no se abre y la entrada queda
  registrada igual. El boleto en el teléfono es una comodidad; cobrar es el
  trabajo.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from .. import formato, tema

logger = logging.getLogger(__name__)


def ofrecer_boleto(padre, folio) -> None:
    """
    Muestra el QR del folio recién emitido, si procede.

    Punto de entrada único desde la pantalla de caja. Concentra aquí todas las
    condiciones y los fallos posibles para que quien lea `registrar_entrada`
    vea una sola línea y no media pantalla de comprobaciones.
    """
    from apps.boletos import qr
    from apps.boletos.publicador import publicacion_activa, publicar_ahora

    if not publicacion_activa():
        return

    try:
        url = qr.url_boleto(folio.uuid)
        imagen_png = qr.png_qr(url)
    except qr.QRNoDisponible as exc:
        # Se registra y se sigue: el folio ya está emitido y el vehículo ya
        # entró. Interrumpir aquí con un error no arregla nada y detiene la fila.
        logger.warning("No se pudo generar el QR del folio %s: %s", folio.uuid, exc)
        return
    except Exception:
        logger.exception("Fallo inesperado al preparar el QR del folio %s", folio.uuid)
        return

    # Se fuerza el envío antes de abrir la ventana: el cliente escanea en
    # segundos y el boleto tiene que existir ya en el servicio.
    publicar_ahora()

    try:
        DialogoBoleto(padre, folio=folio, url=url, imagen_png=imagen_png)
    except tk.TclError:
        # Tk sin soporte de PNG (instalaciones con Tk 8.5). No es motivo para
        # alarmar al operador: se deja constancia y la entrada sigue su curso.
        logger.warning(
            "Esta instalación de Tk no puede mostrar el código QR. "
            "La dirección del boleto es: %s",
            url,
        )


class DialogoBoleto(tk.Toplevel):
    """Ventana modal con el símbolo QR y la dirección del boleto."""

    def __init__(self, padre, *, folio, url: str, imagen_png: bytes):
        super().__init__(padre)

        self.title("Boleto del cliente")
        self.configure(background=tema.SUPERFICIE)
        self.resizable(False, False)
        self.transient(padre)

        marco = ttk.Frame(self, style="Tarjeta.TFrame", padding=tema.ESPACIO_XL)
        marco.grid(sticky="nsew")
        marco.columnconfigure(0, weight=1)

        ttk.Label(
            marco,
            text=f"Boleto de {folio.placa}",
            style="Titulo.TLabel",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            marco,
            text=(
                f"Entrada {formato.hora(folio.entrada_en)}. Pida al cliente que "
                "escanee el código con la cámara de su teléfono para consultar "
                "su tiempo y su importe."
            ),
            style="Secundario.TLabel",
            wraplength=360,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(tema.ESPACIO_S, tema.ESPACIO_L))

        # La referencia a la imagen se guarda en el objeto a propósito: Tk no
        # retiene la suya, y si el recolector de basura se lleva el
        # `PhotoImage` la etiqueta queda en blanco. Es un error clásico de
        # tkinter y no da ningún mensaje.
        self._imagen = tk.PhotoImage(data=imagen_png)
        tk.Label(
            marco,
            image=self._imagen,
            background="#ffffff",
            borderwidth=0,
            highlightthickness=0,
        ).grid(row=2, column=0)

        ttk.Label(
            marco,
            text="Si la cámara no lo reconoce, dicte esta dirección:",
            style="Tenue.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(tema.ESPACIO_L, tema.ESPACIO_XS))

        # Campo de solo lectura en lugar de etiqueta: permite seleccionar y
        # copiar la dirección, que es lo que hace falta para pegarla en un
        # mensaje al cliente.
        campo = ttk.Entry(marco, font=tema.fuente("cuerpo"), width=46)
        campo.insert(0, url)
        campo.configure(state="readonly")
        campo.grid(row=4, column=0, sticky="ew")

        botones = ttk.Frame(marco, style="Tarjeta.TFrame")
        botones.grid(row=5, column=0, sticky="ew", pady=(tema.ESPACIO_L, 0))
        botones.columnconfigure(0, weight=1)
        ttk.Button(
            botones,
            text="Copiar enlace",
            style="Secundario.TButton",
            command=lambda: self._copiar(url),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            botones, text="Listo", style="Principal.TButton", command=self.destroy
        ).grid(row=0, column=1, sticky="e")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self.destroy())
        self._centrar(padre)

        # `grab_set` después de centrar: tomar el foco antes de que la ventana
        # tenga su tamaño definitivo la hace aparecer desplazada.
        self.grab_set()
        self.focus_force()

    def _copiar(self, url: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(url)

    def _centrar(self, padre) -> None:
        self.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_rooty() + (padre.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

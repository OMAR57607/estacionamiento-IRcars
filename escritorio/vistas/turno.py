"""
Pestaña de turno y corte de caja.

Implementa el **cierre ciego**: mientras el turno está abierto, esta pantalla
muestra el avance operativo —folios cobrados, vehículos dentro— pero nunca el
total de efectivo esperado. El operador declara lo que contó y solo entonces se
revela la diferencia.

Esa restricción es el control completo. Si la cifra esperada apareciera en
pantalla, el conteo se ajustaría a ella y el faltante nunca se registraría: la
diferencia dejaría de ser un dato y pasaría a ser un trámite. Por eso la
interfaz no ofrece ninguna vía —ni un botón de «ver total»— para conocerla
antes del conteo.

La presentación refuerza esa misma lógica:

* El avance del turno se muestra como **cifras sueltas** y no como un párrafo.
  Un dato de caja enterrado en una frase se lee mal y se recuerda peor.
* Cerrar el turno es **irreversible**, así que su botón es el único rojo de la
  aplicación: debe distinguirse de todo lo que se pulsa por rutina.
* El resultado del corte se presenta en una **ventana propia**, con la
  diferencia en grande y su interpretación escrita. Antes era un bloque de
  texto alineado con espacios dentro de un aviso del sistema, donde la cifra
  que importa pesaba lo mismo que las demás.
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from tkinter import filedialog, messagebox, ttk

from apps.estacionamiento import services_caja
from apps.estacionamiento.models import EstadoTurno, Turno

from .. import formato, tema
from ..errores import operacion


class VistaTurno(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=tema.ESPACIO_L, style="Fondo.TFrame")
        self.contexto = contexto

        self.columnconfigure(0, weight=3, uniform="turno")
        self.columnconfigure(1, weight=2, uniform="turno")
        self.rowconfigure(1, weight=1)

        self._construir_estado()
        self._construir_acciones()
        self._construir_historial()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_estado(self) -> None:
        self.tarjeta_estado = tema.Tarjeta(
            self, "Turno en curso", relleno=tema.ESPACIO_M
        )
        self.tarjeta_estado.grid(row=0, column=1, sticky="new")
        cuerpo = self.tarjeta_estado.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        encabezado = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew")
        encabezado.columnconfigure(1, weight=1)

        self.pildora = tema.Pildora(encabezado, "")
        self.pildora.grid(row=0, column=0, sticky="w")

        self.subtitulo = ttk.Label(encabezado, text="", style="Secundario.TLabel")
        self.subtitulo.grid(row=0, column=1, sticky="w", padx=(tema.ESPACIO_M, 0))

        # Cifras del avance. Ninguna es un importe cobrado: eso es precisamente
        # lo que el cierre ciego no puede revelar antes del conteo.
        self.metricas = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        self.metricas.grid(row=1, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0))

        self.m_fondo = tema.Metrica(self.metricas, "Cambio inicial en caja")
        self.m_cobrados = tema.Metrica(self.metricas, "Boletos cobrados")
        self.m_cortesias = tema.Metrica(self.metricas, "Salidas sin cobro")
        self.m_dentro = tema.Metrica(self.metricas, "Vehículos dentro")
        for indice, metrica in enumerate(
            (self.m_fondo, self.m_cobrados, self.m_cortesias, self.m_dentro)
        ):
            fila, columna = divmod(indice, 2)
            metrica.grid(
                row=fila,
                column=columna,
                sticky="w",
                padx=(0, tema.ESPACIO_L),
                pady=(0, tema.ESPACIO_M),
            )
            self.metricas.columnconfigure(columna, weight=1)

    def _construir_acciones(self) -> None:
        self.tarjeta_acciones = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        self.tarjeta_acciones.grid(
            row=0, column=0, sticky="new", padx=(0, tema.ESPACIO_M)
        )
        contenedor = self.tarjeta_acciones.cuerpo
        contenedor.columnconfigure(0, weight=1)

        # --- Apertura ------------------------------------------------------
        self.marco_apertura = ttk.Frame(contenedor, style="Tarjeta.TFrame")
        self.marco_apertura.columnconfigure(2, weight=1)

        tema.Paso(
            self.marco_apertura,
            1,
            "Abra su turno al empezar",
            "Cuente el cambio con que inicia la caja y captúrelo. Mientras no "
            "haya turno abierto puede registrar entradas, pero no cobrar "
            "salidas.",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, tema.ESPACIO_M))

        ttk.Label(
            self.marco_apertura,
            text="Cambio con que inicia (billetes y monedas que ya hay en caja)",
            style="Secundario.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, tema.ESPACIO_XS))
        self.fondo = ttk.Entry(
            self.marco_apertura, font=tema.fuente("captura"), width=14
        )
        self.fondo.grid(row=2, column=0, sticky="w")
        self.fondo.insert(0, "0.00")
        self.fondo.bind("<Return>", lambda _e: self.abrir_turno())

        ttk.Button(
            self.marco_apertura,
            text="Abrir turno",
            style="Principal.TButton",
            command=self.abrir_turno,
        ).grid(row=2, column=1, sticky="w", padx=(tema.ESPACIO_M, 0))

        # --- Cierre ciego ---------------------------------------------------
        self.marco_cierre = ttk.Frame(contenedor, style="Tarjeta.TFrame")
        self.marco_cierre.columnconfigure(2, weight=1)

        tema.Paso(
            self.marco_cierre,
            2,
            "Corte su turno al terminar",
            "Saque todo el efectivo de la caja y cuéntelo.",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, tema.ESPACIO_M))

        tema.Aviso(
            self.marco_cierre,
            "Cuente TODO el efectivo que hay en caja, incluido el cambio con "
            "que inició.\n"
            "La diferencia aparece después de capturarlo, nunca antes: por eso "
            "esta pantalla no le dice cuánto debería haber.",
            tono="aviso",
            ancho_texto=620,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, tema.ESPACIO_M))

        ttk.Label(
            self.marco_cierre,
            text="Total contado en caja",
            style="Secundario.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, tema.ESPACIO_XS))
        self.efectivo = ttk.Entry(
            self.marco_cierre, font=tema.fuente("captura"), width=14
        )
        self.efectivo.grid(row=3, column=0, sticky="w")
        self.efectivo.bind("<Return>", lambda _e: self.cerrar_turno())

        ttk.Button(
            self.marco_cierre,
            text="Cerrar turno y cortar",
            style="Peligro.TButton",
            command=self.cerrar_turno,
        ).grid(row=3, column=1, sticky="w", padx=(tema.ESPACIO_M, 0))

        ttk.Label(
            self.marco_cierre,
            text="Es definitivo y queda registrado a su nombre. Los vehículos que "
            "sigan dentro se cobrarán en el turno siguiente.",
            style="Tenue.TLabel",
            wraplength=560,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(tema.ESPACIO_S, 0))

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
        ttk.Label(
            encabezado,
            text="TURNOS QUE YA CORTÓ   ·   doble clic en un renglón para guardar su "
            "comprobante",
            style="Seccion.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            encabezado,
            text="Guardar comprobante…",
            style="Enlace.TButton",
            command=self.guardar_comprobante,
        ).grid(row=0, column=1, sticky="e")

        self.marco_tabla, self.tabla = tema.tabla_con_barra(
            cuerpo,
            (
                ("fecha", "Cierre", 140, "w"),
                ("operador", "Operador", 110, "w"),
                ("fondo", "Cambio inicial", 110, "e"),
                ("cobrado", "Cobrado", 105, "e"),
                ("esperado", "Esperado", 105, "e"),
                ("contado", "Contado", 105, "e"),
                ("diferencia", "Diferencia", 115, "e"),
            ),
            altura=4,
        )
        self.marco_tabla.grid(row=1, column=0, sticky="nsew")
        self.tabla.bind("<Double-1>", lambda _e: self.guardar_comprobante())

        self.vacio = ttk.Label(
            cuerpo,
            text="Todavía no ha cortado ningún turno.",
            style="Tenue.TLabel",
            anchor="center",
        )

    # -------------------------------------------------------------- Acciones
    @operacion("Apertura de turno")
    def abrir_turno(self) -> None:
        fondo = formato.a_decimal(self.fondo.get(), campo="fondo inicial")
        turno = services_caja.abrir_turno(
            operador=self.contexto.operador, fondo_inicial=fondo
        )
        self.contexto.turno = turno
        self.contexto.notificar()
        messagebox.showinfo(
            "Apertura de turno",
            f"Turno abierto con {formato.moneda(fondo)} de cambio en caja.\n"
            "Ya puede cobrar salidas.",
            parent=self.winfo_toplevel(),
        )
        self.refrescar()

    @operacion("Corte de caja")
    def cerrar_turno(self) -> None:
        turno = self.contexto.turno
        if turno is None:
            messagebox.showinfo(
                "Corte de caja",
                "No tiene un turno abierto que cortar.",
                parent=self.winfo_toplevel(),
            )
            return

        efectivo = formato.a_decimal(self.efectivo.get(), campo="efectivo contado")

        # Confirmación explícita: el corte es irreversible y la cifra declarada
        # queda registrada de forma permanente a nombre del operador.
        if not messagebox.askyesno(
            "Confirmar corte",
            f"Declara haber contado {formato.moneda(efectivo)} en caja.\n\n"
            "El corte es definitivo y quedará registrado a su nombre.\n"
            "Si se equivocó al contar, cancele y vuelva a contar.\n"
            "¿Confirma la cifra?",
            parent=self.winfo_toplevel(),
        ):
            return

        turno, resumen = services_caja.cerrar_turno(
            turno_id=turno.pk, efectivo_declarado=efectivo
        )
        self.contexto.turno = None
        self.contexto.notificar()
        self.efectivo.delete(0, tk.END)
        self.refrescar()
        DialogoCorte(self.winfo_toplevel(), turno, resumen, self._texto_comprobante)

    @operacion("Comprobante")
    def guardar_comprobante(self) -> None:
        seleccion = self.tabla.selection()
        if not seleccion:
            messagebox.showinfo(
                "Comprobante",
                "Seleccione un turno de la lista.",
                parent=self.winfo_toplevel(),
            )
            return

        turno = Turno.objects.select_related("operador").get(
            pk=int(self.tabla.item(seleccion[0], "text"))
        )
        self._exportar_comprobante(turno)

    def _exportar_comprobante(self, turno: Turno) -> None:
        destino = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="Guardar comprobante de corte",
            defaultextension=".txt",
            initialfile=f"corte_turno_{turno.pk}_{turno.cerrado_en:%Y%m%d_%H%M}.txt",
            filetypes=[("Texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not destino:
            return

        with open(destino, "w", encoding="utf-8") as archivo:
            archivo.write(self._texto_comprobante(turno))

        messagebox.showinfo(
            "Comprobante",
            f"Comprobante guardado en:\n{destino}",
            parent=self.winfo_toplevel(),
        )

    def _texto_comprobante(self, turno: Turno) -> str:
        """
        Comprobante en texto plano, imprimible en cualquier impresora.

        Se eligió texto y no PDF a propósito: una caseta suele tener una
        impresora térmica de tickets, que imprime texto directamente y no
        interpreta formatos ricos.
        """
        lineas = [
            "=" * 46,
            "       CORTE DE CAJA — ESTACIONAMIENTO IRCARS",
            "=" * 46,
            f"Turno:            {turno.pk}",
            f"Operador:         {turno.operador}",
            f"Apertura:         {formato.fecha_hora(turno.abierto_en)}",
            f"Cierre:           {formato.fecha_hora(turno.cerrado_en)}",
            "-" * 46,
            f"Fondo inicial:    {formato.moneda(turno.fondo_inicial):>18}",
            f"Cobrado:          {formato.moneda(turno.total_cobrado):>18}",
            f"Esperado:         {formato.moneda(turno.efectivo_esperado):>18}",
            f"Contado:          {formato.moneda(turno.efectivo_declarado):>18}",
            "-" * 46,
            f"DIFERENCIA:       {formato.moneda_con_signo(turno.diferencia):>18}",
            "-" * 46,
            f"Folios cobrados:  {turno.folios_cobrados}",
            f"Cortesías:        {turno.folios_cortesia}",
        ]
        if turno.observaciones.strip():
            lineas += ["-" * 46, "Observaciones:", turno.observaciones.strip()]
        lineas += [
            "=" * 46,
            "",
            "",
            "   _______________________      _______________________",
            "        Firma del operador           Firma de quien recibe",
            "",
        ]
        return "\n".join(lineas)

    # ------------------------------------------------------------- Refresco
    @operacion("Turno")
    def refrescar(self) -> None:
        turno = self.contexto.refrescar_turno()

        if turno is None:
            self.pildora.actualizar(
                "SIN TURNO ABIERTO", fondo=tema.PELIGRO_SUAVE, color=tema.PELIGRO
            )
            self.subtitulo.configure(
                text="Un cobro sin turno quedaría fuera del corte, así que la caja "
                "no lo permite. Abra el turno con el campo de abajo."
            )
            self.m_fondo.actualizar("—")
            self.m_cobrados.actualizar("—")
            self.m_cortesias.actualizar("—")
            self.m_dentro.actualizar("—")

            self.marco_cierre.grid_forget()
            self.marco_apertura.grid(row=0, column=0, sticky="ew")
            self.fondo.focus_set()
        else:
            avance = services_caja.resumen_operativo(turno)
            self.pildora.actualizar(
                f"TURNO {turno.pk} ABIERTO", fondo=tema.EXITO_SUAVE, color=tema.EXITO
            )
            self.subtitulo.configure(
                text=f"{turno.operador}   ·   desde {formato.fecha_hora(turno.abierto_en)}"
            )
            self.m_fondo.actualizar(formato.moneda(turno.fondo_inicial))
            self.m_cobrados.actualizar(str(avance["folios_cobrados"]))
            self.m_cortesias.actualizar(str(avance["folios_cortesia"]))
            self.m_dentro.actualizar(str(avance["folios_abiertos_en_patio"]))

            self.marco_apertura.grid_forget()
            self.marco_cierre.grid(row=0, column=0, sticky="ew")

        self._recargar_historial()

    def _recargar_historial(self) -> None:
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)

        cerrados = (
            Turno.objects.select_related("operador")
            .filter(estado=EstadoTurno.CERRADO)
            .order_by("-cerrado_en")[:200]
        )
        total = 0
        for indice, turno in enumerate(cerrados):
            total += 1
            diferencia = turno.diferencia or Decimal("0.00")
            # Resaltado por resultado: el faltante debe salir a la vista sin que
            # nadie tenga que comparar dos columnas.
            resultado = ""
            if diferencia < 0:
                resultado = "faltante"
            elif diferencia > 0:
                resultado = "sobrante"

            self.tabla.insert(
                "",
                "end",
                text=str(turno.pk),
                tags=tema.etiquetas_de_fila(indice, resultado),
                values=(
                    formato.fecha_hora(turno.cerrado_en),
                    turno.operador,
                    formato.moneda(turno.fondo_inicial),
                    formato.moneda(turno.total_cobrado),
                    formato.moneda(turno.efectivo_esperado),
                    formato.moneda(turno.efectivo_declarado),
                    formato.moneda_con_signo(turno.diferencia),
                ),
            )

        if total:
            self.vacio.grid_remove()
            self.marco_tabla.grid()
        else:
            self.marco_tabla.grid_remove()
            self.vacio.grid(row=1, column=0, sticky="nsew", pady=tema.ESPACIO_XL)

    def al_entrar(self) -> None:
        self.refrescar()


class DialogoCorte(tk.Toplevel):
    """
    Resultado del corte, con la diferencia como protagonista.

    Es el momento en que el operador entrega la caja, y la cifra que define si
    puede hacerlo —la diferencia— debe leerse antes que ninguna otra. Un aviso
    del sistema con ocho renglones alineados a espacios no lo conseguía: ahí
    todas las cantidades pesan igual.
    """

    def __init__(self, padre, turno, resumen, texto_comprobante):
        super().__init__(padre)
        self._turno = turno
        self._texto_comprobante = texto_comprobante

        self.title("Resultado del corte")
        self.configure(background=tema.SUPERFICIE)
        self.resizable(False, False)
        self.transient(padre)
        self.grab_set()

        if resumen.cuadra:
            titular, color, fondo = "LA CAJA CUADRA", tema.EXITO, tema.EXITO_SUAVE
            veredicto = "Lo que contó coincide exactamente con lo que debía haber."
        elif resumen.hay_faltante:
            titular, color, fondo = "FALTA DINERO", tema.PELIGRO, tema.PELIGRO_SUAVE
            veredicto = (
                "Hay menos efectivo del que debería. Recuente antes de entregar "
                "el turno y deje constancia de la diferencia."
            )
        else:
            titular, color, fondo = "SOBRA DINERO", tema.AVISO, tema.AVISO_SUAVE
            veredicto = (
                "Hay más efectivo del que debería. Revise si quedó alguna salida "
                "sin registrar."
            )

        marco = ttk.Frame(self, style="Tarjeta.TFrame", padding=tema.ESPACIO_XL)
        marco.grid(sticky="nsew")
        marco.columnconfigure(0, weight=1)

        # --- Veredicto -------------------------------------------------------
        banda = tk.Frame(marco, background=fondo)
        banda.grid(row=0, column=0, sticky="ew")
        tk.Label(
            banda,
            text=titular,
            background=fondo,
            foreground=color,
            font=tema.fuente("seccion"),
            anchor="w",
            padx=16,
        ).pack(fill="x", pady=(12, 0))
        tk.Label(
            banda,
            text=formato.moneda_con_signo(resumen.diferencia),
            background=fondo,
            foreground=color,
            font=tema.fuente("display"),
            anchor="w",
            padx=16,
        ).pack(fill="x")
        tk.Label(
            banda,
            text=veredicto,
            background=fondo,
            foreground=color,
            font=tema.fuente("cuerpo"),
            anchor="w",
            justify="left",
            padx=16,
        ).pack(fill="x", pady=(0, 14))

        # --- Desglose --------------------------------------------------------
        detalle = ttk.Frame(marco, style="Tarjeta.TFrame")
        detalle.grid(row=1, column=0, sticky="ew", pady=(tema.ESPACIO_L, 0))
        detalle.columnconfigure(1, weight=1)

        renglones = (
            ("Operador", str(turno.operador)),
            ("Apertura", formato.fecha_hora(turno.abierto_en)),
            ("Cierre", formato.fecha_hora(turno.cerrado_en)),
            ("", ""),
            ("Cambio inicial en caja", formato.moneda(resumen.fondo_inicial)),
            ("Cobrado en el turno", formato.moneda(resumen.total_cobrado)),
            ("Debería haber en caja", formato.moneda(resumen.efectivo_esperado)),
            ("Usted contó", formato.moneda(resumen.efectivo_declarado)),
            ("", ""),
            ("Boletos cobrados", str(resumen.folios_cobrados)),
            ("Salidas sin cobro", str(resumen.folios_cortesia)),
        )
        tema.renglones_de_datos(detalle, renglones)

        # --- Acciones --------------------------------------------------------
        botones = ttk.Frame(marco, style="Tarjeta.TFrame")
        botones.grid(row=2, column=0, sticky="ew", pady=(tema.ESPACIO_L, 0))
        botones.columnconfigure(0, weight=1)

        ttk.Button(
            botones,
            text="Guardar comprobante…",
            style="Secundario.TButton",
            command=self._guardar,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            botones, text="Entendido", style="Principal.TButton", command=self.destroy
        ).grid(row=0, column=1, sticky="e")

        self.bind("<Return>", lambda _e: self.destroy())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._centrar(padre)
        self.focus_force()

    def _guardar(self) -> None:
        destino = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar comprobante de corte",
            defaultextension=".txt",
            initialfile=(
                f"corte_turno_{self._turno.pk}_"
                f"{self._turno.cerrado_en:%Y%m%d_%H%M}.txt"
            ),
            filetypes=[("Texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not destino:
            return
        with open(destino, "w", encoding="utf-8") as archivo:
            archivo.write(self._texto_comprobante(self._turno))
        messagebox.showinfo(
            "Comprobante", f"Comprobante guardado en:\n{destino}", parent=self
        )

    def _centrar(self, padre) -> None:
        self.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_rooty() + (padre.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

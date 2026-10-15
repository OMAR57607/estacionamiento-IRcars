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

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._construir_estado()
        self._construir_acciones()
        self._construir_historial()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_estado(self) -> None:
        self.tarjeta_estado = tema.Tarjeta(self, "Turno en curso")
        self.tarjeta_estado.grid(row=0, column=0, sticky="ew")
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

        self.m_fondo = tema.Metrica(self.metricas, "Fondo inicial")
        self.m_cobrados = tema.Metrica(self.metricas, "Folios cobrados")
        self.m_cortesias = tema.Metrica(self.metricas, "Cortesías")
        self.m_dentro = tema.Metrica(self.metricas, "Vehículos dentro")
        for columna, metrica in enumerate(
            (self.m_fondo, self.m_cobrados, self.m_cortesias, self.m_dentro)
        ):
            metrica.grid(row=0, column=columna, sticky="w", padx=(0, tema.ESPACIO_XL))
            self.metricas.columnconfigure(columna, weight=1)

    def _construir_acciones(self) -> None:
        self.tarjeta_acciones = tema.Tarjeta(self, "Acciones")
        self.tarjeta_acciones.grid(row=1, column=0, sticky="ew", pady=(tema.ESPACIO_M, 0))
        contenedor = self.tarjeta_acciones.cuerpo
        contenedor.columnconfigure(0, weight=1)

        # --- Apertura ------------------------------------------------------
        self.marco_apertura = ttk.Frame(contenedor, style="Tarjeta.TFrame")
        self.marco_apertura.columnconfigure(2, weight=1)

        ttk.Label(
            self.marco_apertura,
            text="Cuente el cambio con que inicia la caja y declárelo. Sin turno "
            "abierto no es posible cobrar salidas.",
            style="Secundario.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, tema.ESPACIO_M))

        ttk.Label(
            self.marco_apertura, text="Fondo inicial", style="Secundario.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(0, tema.ESPACIO_XS))
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

        aviso = tk.Frame(
            self.marco_cierre,
            background=tema.AVISO_SUAVE,
            highlightbackground="#efd9ae",
            highlightthickness=1,
            bd=0,
        )
        aviso.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, tema.ESPACIO_M))
        tk.Label(
            aviso,
            text=(
                "Cuente el efectivo que hay en caja —incluido el fondo inicial— y "
                "capture el total.\nLa diferencia se revela después de registrarlo, "
                "nunca antes: es lo que hace válido el conteo."
            ),
            background=tema.AVISO_SUAVE,
            foreground=tema.AVISO,
            font=tema.fuente("cuerpo"),
            justify="left",
            anchor="w",
            padx=14,
            pady=10,
        ).pack(fill="x")

        ttk.Label(
            self.marco_cierre, text="Efectivo contado", style="Secundario.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(0, tema.ESPACIO_XS))
        self.efectivo = ttk.Entry(
            self.marco_cierre, font=tema.fuente("captura"), width=14
        )
        self.efectivo.grid(row=2, column=0, sticky="w")
        self.efectivo.bind("<Return>", lambda _e: self.cerrar_turno())

        ttk.Button(
            self.marco_cierre,
            text="Cerrar turno y cortar",
            style="Peligro.TButton",
            command=self.cerrar_turno,
        ).grid(row=2, column=1, sticky="w", padx=(tema.ESPACIO_M, 0))

        ttk.Label(
            self.marco_cierre,
            text="Acción definitiva: el corte queda registrado a su nombre.",
            style="Tenue.TLabel",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(tema.ESPACIO_S, 0))

    def _construir_historial(self) -> None:
        tarjeta = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        tarjeta.grid(row=2, column=0, sticky="nsew", pady=(tema.ESPACIO_M, 0))
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(1, weight=1)

        encabezado = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew", pady=(0, tema.ESPACIO_S))
        encabezado.columnconfigure(0, weight=1)
        ttk.Label(encabezado, text="TURNOS CORTADOS", style="Seccion.TLabel").grid(
            row=0, column=0, sticky="w"
        )
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
                ("fondo", "Fondo", 95, "e"),
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
            text="Todavía no hay turnos cortados.",
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
            f"Turno abierto con fondo de {formato.moneda(fondo)}.\n"
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
                text="Un cobro sin turno queda fuera del corte, así que la caja "
                "no lo permite."
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
            veredicto = "El efectivo contado coincide exactamente con lo esperado."
        elif resumen.hay_faltante:
            titular, color, fondo = "FALTANTE", tema.PELIGRO, tema.PELIGRO_SUAVE
            veredicto = (
                "Recuente antes de entregar el turno y documente la diferencia."
            )
        else:
            titular, color, fondo = "SOBRANTE", tema.AVISO, tema.AVISO_SUAVE
            veredicto = "Verifique si se omitió registrar algún cobro."

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
            ("Fondo inicial", formato.moneda(resumen.fondo_inicial)),
            ("Cobrado en el turno", formato.moneda(resumen.total_cobrado)),
            ("Efectivo esperado", formato.moneda(resumen.efectivo_esperado)),
            ("Efectivo contado", formato.moneda(resumen.efectivo_declarado)),
            ("", ""),
            ("Folios cobrados", str(resumen.folios_cobrados)),
            ("Folios en cortesía", str(resumen.folios_cortesia)),
        )
        for fila, (etiqueta, valor) in enumerate(renglones):
            if not etiqueta:
                ttk.Separator(detalle, orient="horizontal").grid(
                    row=fila, column=0, columnspan=2, sticky="ew", pady=tema.ESPACIO_S
                )
                continue
            ttk.Label(detalle, text=etiqueta, style="Secundario.TLabel").grid(
                row=fila, column=0, sticky="w", pady=2
            )
            ttk.Label(
                detalle, text=valor, font=tema.fuente("cuerpo_fuerte")
            ).grid(row=fila, column=1, sticky="e", pady=2)

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

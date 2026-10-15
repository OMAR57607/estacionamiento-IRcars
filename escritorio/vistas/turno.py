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
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from tkinter import filedialog, messagebox, ttk

from apps.estacionamiento import services_caja
from apps.estacionamiento.models import EstadoTurno, Turno

from .. import formato
from ..errores import operacion


class VistaTurno(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=12)
        self.contexto = contexto

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._construir_estado()
        self._construir_acciones()
        self._construir_historial()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_estado(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Turno en curso ", padding=12)
        grupo.grid(row=0, column=0, sticky="ew")
        grupo.columnconfigure(0, weight=1)

        self.etiqueta_estado = ttk.Label(
            grupo, text="", justify="left", font=("TkDefaultFont", 11)
        )
        self.etiqueta_estado.grid(row=0, column=0, sticky="w")

    def _construir_acciones(self) -> None:
        self.grupo_acciones = ttk.LabelFrame(self, text=" Acciones ", padding=12)
        self.grupo_acciones.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.grupo_acciones.columnconfigure(1, weight=1)

        # --- Apertura ------------------------------------------------------
        self.marco_apertura = ttk.Frame(self.grupo_acciones)
        ttk.Label(self.marco_apertura, text="Fondo inicial (cambio en caja):").grid(
            row=0, column=0, sticky="w"
        )
        self.fondo = ttk.Entry(self.marco_apertura, font=("TkDefaultFont", 13), width=14)
        self.fondo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.fondo.insert(0, "0.00")
        ttk.Button(
            self.marco_apertura, text="Abrir turno", command=self.abrir_turno
        ).grid(row=0, column=2, padx=(12, 0))

        # --- Cierre ciego ---------------------------------------------------
        self.marco_cierre = ttk.Frame(self.grupo_acciones)
        ttk.Label(
            self.marco_cierre,
            text=(
                "Cuente el efectivo en caja —incluido el fondo inicial— y capture\n"
                "el total. El sistema revelará la diferencia después de registrarlo."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(self.marco_cierre, text="Efectivo contado:").grid(
            row=1, column=0, sticky="w"
        )
        self.efectivo = ttk.Entry(self.marco_cierre, font=("TkDefaultFont", 13), width=14)
        self.efectivo.grid(row=1, column=1, sticky="w", padx=(8, 0))
        self.efectivo.bind("<Return>", lambda _e: self.cerrar_turno())
        ttk.Button(
            self.marco_cierre,
            text="Cerrar turno y cortar",
            command=self.cerrar_turno,
        ).grid(row=1, column=2, padx=(12, 0))

    def _construir_historial(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Turnos cortados ", padding=8)
        grupo.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        grupo.columnconfigure(0, weight=1)
        grupo.rowconfigure(0, weight=1)

        columnas = ("fecha", "operador", "fondo", "cobrado", "esperado", "contado", "diferencia")
        self.tabla = ttk.Treeview(
            grupo, columns=columnas, show="headings", selectmode="browse"
        )
        for columna, titulo, ancho, anclaje in (
            ("fecha", "Cierre", 130, "w"),
            ("operador", "Operador", 110, "w"),
            ("fondo", "Fondo", 90, "e"),
            ("cobrado", "Cobrado", 100, "e"),
            ("esperado", "Esperado", 100, "e"),
            ("contado", "Contado", 100, "e"),
            ("diferencia", "Diferencia", 110, "e"),
        ):
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=anclaje)
        self.tabla.grid(row=0, column=0, sticky="nsew")

        # Resaltado por resultado: el faltante debe salir a la vista sin que
        # nadie tenga que comparar dos columnas.
        self.tabla.tag_configure("faltante", foreground="#a01818")
        self.tabla.tag_configure("sobrante", foreground="#8a5a00")

        barra = ttk.Scrollbar(grupo, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=barra.set)
        barra.grid(row=0, column=1, sticky="ns")

        pie = ttk.Frame(grupo)
        pie.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        pie.columnconfigure(0, weight=1)
        ttk.Button(
            pie, text="Guardar comprobante del turno seleccionado…",
            command=self.guardar_comprobante,
        ).grid(row=0, column=1, sticky="e")

    # -------------------------------------------------------------- Acciones
    @operacion("Apertura de turno")
    def abrir_turno(self) -> None:
        fondo = formato.a_decimal(self.fondo.get(), campo="fondo inicial")
        turno = services_caja.abrir_turno(
            operador=self.contexto.operador, fondo_inicial=fondo
        )
        self.contexto.turno = turno
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
        self.efectivo.delete(0, tk.END)
        self.refrescar()
        self._mostrar_corte(turno, resumen)

    def _mostrar_corte(self, turno: Turno, resumen) -> None:
        """Revela el resultado del corte, ya con la diferencia calculada."""
        if resumen.cuadra:
            titulo, veredicto = "Corte cuadrado", "La caja cuadra exactamente."
        elif resumen.hay_faltante:
            titulo = "Corte con FALTANTE"
            veredicto = (
                f"Falta {formato.moneda(abs(resumen.diferencia))} en caja.\n"
                "Recuente antes de entregar el turno y documente la diferencia."
            )
        else:
            titulo = "Corte con sobrante"
            veredicto = (
                f"Hay {formato.moneda(resumen.diferencia)} de más en caja.\n"
                "Verifique si se omitió registrar algún cobro."
            )

        detalle = (
            f"Operador:            {turno.operador}\n"
            f"Apertura:            {formato.fecha_hora(turno.abierto_en)}\n"
            f"Cierre:              {formato.fecha_hora(turno.cerrado_en)}\n\n"
            f"Fondo inicial:       {formato.moneda(resumen.fondo_inicial)}\n"
            f"Cobrado en el turno: {formato.moneda(resumen.total_cobrado)}\n"
            f"Efectivo esperado:   {formato.moneda(resumen.efectivo_esperado)}\n"
            f"Efectivo contado:    {formato.moneda(resumen.efectivo_declarado)}\n"
            f"DIFERENCIA:          {formato.moneda_con_signo(resumen.diferencia)}\n\n"
            f"Folios cobrados:     {resumen.folios_cobrados}\n"
            f"Folios en cortesía:  {resumen.folios_cortesia}\n\n"
            f"{veredicto}"
        )
        messagebox.showinfo(titulo, detalle, parent=self.winfo_toplevel())

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
            self.etiqueta_estado.configure(
                text=(
                    "Sin turno abierto.\n"
                    "Debe abrir turno antes de cobrar salidas: un ingreso sin "
                    "turno queda fuera del corte."
                )
            )
            self.marco_cierre.grid_forget()
            self.marco_apertura.grid(row=0, column=0, columnspan=3, sticky="w")
            self.fondo.focus_set()
        else:
            avance = services_caja.resumen_operativo(turno)
            self.etiqueta_estado.configure(
                text=(
                    f"Turno {turno.pk} ABIERTO · operador {turno.operador}\n"
                    f"Apertura: {formato.fecha_hora(turno.abierto_en)} · "
                    f"Fondo inicial: {formato.moneda(turno.fondo_inicial)}\n"
                    f"Folios cobrados en el turno: {avance['folios_cobrados']} "
                    f"(cortesías: {avance['folios_cortesia']})\n"
                    f"Vehículos dentro ahora: {avance['folios_abiertos_en_patio']}"
                )
            )
            self.marco_apertura.grid_forget()
            self.marco_cierre.grid(row=0, column=0, columnspan=3, sticky="w")

        self._recargar_historial()

    def _recargar_historial(self) -> None:
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)

        cerrados = (
            Turno.objects.select_related("operador")
            .filter(estado=EstadoTurno.CERRADO)
            .order_by("-cerrado_en")[:200]
        )
        for turno in cerrados:
            diferencia = turno.diferencia or Decimal("0.00")
            etiquetas = ()
            if diferencia < 0:
                etiquetas = ("faltante",)
            elif diferencia > 0:
                etiquetas = ("sobrante",)

            self.tabla.insert(
                "",
                "end",
                text=str(turno.pk),
                tags=etiquetas,
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

    def al_entrar(self) -> None:
        self.refrescar()

"""
Pestaña de reportes administrativos.

Responde la pregunta del dueño: cuánto se cobró, cuántos vehículos entraron y
cuánta cortesía se otorgó en un periodo. La exportación a CSV existe porque esa
información termina invariablemente en una hoja de cálculo o en manos del
contador, y transcribirla a mano introduce errores.
"""
from __future__ import annotations

import csv
from datetime import datetime, time, timedelta
from tkinter import filedialog, messagebox, ttk

from django.utils import timezone

from apps.estacionamiento import services_caja
from apps.estacionamiento.models import EstadoFolio, Folio

from .. import formato
from ..errores import operacion

FORMATO_FECHA = "%d/%m/%Y"


class VistaReportes(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=12)
        self.contexto = contexto
        self._folios = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._construir_filtros()
        self._construir_totales()
        self._construir_tabla()
        self.generar()

    # ------------------------------------------------------------------ UI
    def _construir_filtros(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Periodo ", padding=12)
        grupo.grid(row=0, column=0, sticky="ew")

        hoy = timezone.localtime().date()

        ttk.Label(grupo, text="Desde (dd/mm/aaaa):").grid(row=0, column=0, sticky="w")
        self.desde = ttk.Entry(grupo, width=14)
        self.desde.grid(row=0, column=1, padx=(8, 16))
        self.desde.insert(0, hoy.strftime(FORMATO_FECHA))

        ttk.Label(grupo, text="Hasta:").grid(row=0, column=2, sticky="w")
        self.hasta = ttk.Entry(grupo, width=14)
        self.hasta.grid(row=0, column=3, padx=(8, 16))
        self.hasta.insert(0, hoy.strftime(FORMATO_FECHA))

        ttk.Button(grupo, text="Generar", command=self.generar).grid(row=0, column=4)

        # Atajos de periodo: cubren el 90 % de las consultas reales y evitan
        # teclear fechas, donde se cometen la mayoría de los errores.
        atajos = ttk.Frame(grupo)
        atajos.grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 0))
        for texto, dias in (("Hoy", 0), ("Últimos 7 días", 6), ("Últimos 30 días", 29)):
            ttk.Button(
                atajos,
                text=texto,
                command=lambda d=dias: self._aplicar_atajo(d),
            ).grid(row=0, column=atajos.grid_size()[0], padx=(0, 8))

    def _construir_totales(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Totales del periodo ", padding=12)
        grupo.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.etiqueta_totales = ttk.Label(
            grupo, text="", justify="left", font=("TkDefaultFont", 11)
        )
        self.etiqueta_totales.grid(row=0, column=0, sticky="w")

    def _construir_tabla(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Folios cobrados ", padding=8)
        grupo.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        grupo.columnconfigure(0, weight=1)
        grupo.rowconfigure(0, weight=1)

        columnas = ("placa", "entrada", "salida", "permanencia", "importe", "operador")
        self.tabla = ttk.Treeview(grupo, columns=columnas, show="headings")
        for columna, titulo, ancho, anclaje in (
            ("placa", "Placa", 120, "w"),
            ("entrada", "Entrada", 130, "w"),
            ("salida", "Salida", 130, "w"),
            ("permanencia", "Permanencia", 120, "w"),
            ("importe", "Importe", 100, "e"),
            ("operador", "Cobró", 110, "w"),
        ):
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=anclaje)
        self.tabla.grid(row=0, column=0, sticky="nsew")

        barra = ttk.Scrollbar(grupo, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=barra.set)
        barra.grid(row=0, column=1, sticky="ns")

        pie = ttk.Frame(grupo)
        pie.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        pie.columnconfigure(0, weight=1)
        ttk.Button(pie, text="Exportar a CSV…", command=self.exportar).grid(
            row=0, column=1, sticky="e"
        )

    # -------------------------------------------------------------- Acciones
    def _aplicar_atajo(self, dias_atras: int) -> None:
        hoy = timezone.localtime().date()
        self.desde.delete(0, "end")
        self.desde.insert(0, (hoy - timedelta(days=dias_atras)).strftime(FORMATO_FECHA))
        self.hasta.delete(0, "end")
        self.hasta.insert(0, hoy.strftime(FORMATO_FECHA))
        self.generar()

    def _leer_rango(self) -> tuple[datetime, datetime]:
        """
        Interpreta las fechas capturadas como un día completo local.

        El límite inferior es 00:00 y el superior 23:59:59 del día indicado: un
        reporte «del 1 al 5» debe incluir todo el día 5, no detenerse a
        medianoche del 4. Las marcas se convierten a la zona horaria local
        configurada, no a UTC, porque el corte de día es el del negocio.
        """
        try:
            inicio = datetime.strptime(self.desde.get().strip(), FORMATO_FECHA).date()
            fin = datetime.strptime(self.hasta.get().strip(), FORMATO_FECHA).date()
        except ValueError as exc:
            raise ValueError(
                "Las fechas deben tener el formato dd/mm/aaaa, por ejemplo 05/03/2026."
            ) from exc

        zona = timezone.get_current_timezone()
        return (
            timezone.make_aware(datetime.combine(inicio, time.min), zona),
            timezone.make_aware(datetime.combine(fin, time.max), zona),
        )

    @operacion("Reporte")
    def generar(self) -> None:
        desde, hasta = self._leer_rango()
        datos = services_caja.reporte_por_rango(desde, hasta)

        self.etiqueta_totales.configure(
            text=(
                f"Total cobrado:        {formato.moneda(datos['total_cobrado'])}\n"
                f"Folios cobrados:      {datos['folios_cobrados']}  "
                f"(cortesías: {datos['folios_cortesia']})\n"
                f"Importe promedio:     {formato.moneda(datos['importe_promedio'])}\n"
                f"Permanencia promedio: {formato.duracion(int(datos['minutos_promedio']))}\n"
                f"Turnos cortados:      {datos['turnos_cerrados']}"
            )
        )

        self._folios = list(
            Folio.objects.select_related("operador_salida")
            .filter(
                estado=EstadoFolio.CERRADO, salida_en__gte=desde, salida_en__lte=hasta
            )
            .order_by("-salida_en")[:2000]
        )

        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        for folio in self._folios:
            self.tabla.insert(
                "",
                "end",
                values=(
                    folio.placa,
                    formato.fecha_hora(folio.entrada_en),
                    formato.fecha_hora(folio.salida_en),
                    formato.duracion(folio.minutos),
                    formato.moneda(folio.importe),
                    folio.operador_salida or "—",
                ),
            )

    @operacion("Exportación")
    def exportar(self) -> None:
        if not self._folios:
            messagebox.showinfo(
                "Exportación",
                "El periodo seleccionado no tiene folios cobrados.",
                parent=self.winfo_toplevel(),
            )
            return

        destino = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="Exportar folios cobrados",
            defaultextension=".csv",
            initialfile=f"folios_{timezone.localtime():%Y%m%d_%H%M}.csv",
            filetypes=[("CSV", "*.csv"), ("Todos los archivos", "*.*")],
        )
        if not destino:
            return

        # utf-8-sig: Excel en Windows interpreta correctamente los acentos con
        # la marca de orden de bytes. Sin ella, «Automóvil» se ve corrupto y el
        # contador asume que el archivo está dañado.
        with open(destino, "w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(
                [
                    "folio",
                    "placa",
                    "entrada",
                    "salida",
                    "minutos",
                    "importe",
                    "pago_recibido",
                    "cambio",
                    "tarifa_cuota_hora",
                    "operador_entrada",
                    "operador_salida",
                    "turno",
                ]
            )
            for folio in self._folios:
                escritor.writerow(
                    [
                        folio.uuid,
                        folio.placa,
                        formato.fecha_hora(folio.entrada_en),
                        formato.fecha_hora(folio.salida_en),
                        folio.minutos,
                        folio.importe,
                        folio.pago_recibido,
                        folio.cambio,
                        folio.tarifa_cuota_hora,
                        folio.operador_entrada_id or "",
                        folio.operador_salida or "",
                        folio.turno_id or "",
                    ]
                )

        messagebox.showinfo(
            "Exportación",
            f"Se exportaron {len(self._folios)} folios a:\n{destino}",
            parent=self.winfo_toplevel(),
        )

    def al_entrar(self) -> None:
        self.generar()

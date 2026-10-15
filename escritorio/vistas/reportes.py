"""
Pestaña de reportes administrativos.

Responde la pregunta del dueño: cuánto se cobró, cuántos vehículos entraron y
cuánta cortesía se otorgó en un periodo. La exportación a CSV existe porque esa
información termina invariablemente en una hoja de cálculo o en manos del
contador, y transcribirla a mano introduce errores.

Dos decisiones de presentación:

* **Los totales son cifras, no un párrafo.** Es la pantalla que se consulta de
  pie, para contestar «¿cómo vamos hoy?». Un bloque de texto obliga a leerlo
  entero para encontrar un dato; cuatro cifras separadas se localizan de un
  vistazo.
* **El periodo se elige con botones.** Teclear fechas es donde se cometen los
  errores, y «hoy», «esta semana» y «este mes» cubren casi todas las consultas
  reales. Los campos siguen ahí para el caso que no cubren.
"""
from __future__ import annotations

import csv
from datetime import datetime, time, timedelta
from tkinter import filedialog, messagebox, ttk

from django.utils import timezone

from apps.estacionamiento import services_caja
from apps.estacionamiento.models import EstadoFolio, Folio

from .. import formato, tema
from ..errores import operacion

FORMATO_FECHA = "%d/%m/%Y"

ATAJOS = (("Hoy", 0), ("Últimos 7 días", 6), ("Últimos 30 días", 29))


class VistaReportes(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=tema.ESPACIO_L, style="Fondo.TFrame")
        self.contexto = contexto
        self._folios = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._construir_cabecera()
        self._construir_tabla()
        self.generar()

    # ------------------------------------------------------------------ UI
    def _construir_cabecera(self) -> None:
        tarjeta = tema.Tarjeta(self, "Periodo")
        tarjeta.grid(row=0, column=0, sticky="ew")
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        # --- Selección del periodo -----------------------------------------
        filtros = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        filtros.grid(row=0, column=0, sticky="ew")
        filtros.columnconfigure(5, weight=1)

        hoy = timezone.localtime().date()

        ttk.Label(filtros, text="Desde", style="Secundario.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, tema.ESPACIO_S)
        )
        self.desde = ttk.Entry(filtros, width=12)
        self.desde.grid(row=0, column=1, padx=(0, tema.ESPACIO_L))
        self.desde.insert(0, hoy.strftime(FORMATO_FECHA))
        self.desde.bind("<Return>", lambda _e: self.generar())

        ttk.Label(filtros, text="Hasta", style="Secundario.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, tema.ESPACIO_S)
        )
        self.hasta = ttk.Entry(filtros, width=12)
        self.hasta.grid(row=0, column=3, padx=(0, tema.ESPACIO_M))
        self.hasta.insert(0, hoy.strftime(FORMATO_FECHA))
        self.hasta.bind("<Return>", lambda _e: self.generar())

        ttk.Button(
            filtros, text="Generar", style="Principal.TButton", command=self.generar
        ).grid(row=0, column=4, sticky="w")

        atajos = ttk.Frame(filtros, style="Tarjeta.TFrame")
        atajos.grid(row=0, column=5, sticky="e")
        for columna, (texto, dias) in enumerate(ATAJOS):
            ttk.Button(
                atajos,
                text=texto,
                style="Secundario.TButton",
                command=lambda d=dias: self._aplicar_atajo(d),
            ).grid(row=0, column=columna, padx=(tema.ESPACIO_XS, 0))

        ttk.Separator(cuerpo, orient="horizontal").grid(
            row=1, column=0, sticky="ew", pady=tema.ESPACIO_L
        )

        # --- Totales ---------------------------------------------------------
        metricas = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        metricas.grid(row=2, column=0, sticky="ew")

        self.m_total = tema.Metrica(metricas, "Total cobrado")
        self.m_folios = tema.Metrica(metricas, "Folios cobrados")
        self.m_cortesias = tema.Metrica(metricas, "Cortesías")
        self.m_promedio = tema.Metrica(metricas, "Importe promedio")
        self.m_permanencia = tema.Metrica(metricas, "Permanencia promedio")
        self.m_turnos = tema.Metrica(metricas, "Turnos cortados")

        for columna, metrica in enumerate(
            (
                self.m_total,
                self.m_folios,
                self.m_cortesias,
                self.m_promedio,
                self.m_permanencia,
                self.m_turnos,
            )
        ):
            metrica.grid(row=0, column=columna, sticky="w", padx=(0, tema.ESPACIO_XL))
            metricas.columnconfigure(columna, weight=1)

    def _construir_tabla(self) -> None:
        tarjeta = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        tarjeta.grid(row=1, column=0, sticky="nsew", pady=(tema.ESPACIO_M, 0))
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(1, weight=1)

        encabezado = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        encabezado.grid(row=0, column=0, sticky="ew", pady=(0, tema.ESPACIO_S))
        encabezado.columnconfigure(0, weight=1)
        ttk.Label(encabezado, text="FOLIOS COBRADOS", style="Seccion.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            encabezado,
            text="Exportar a CSV…",
            style="Enlace.TButton",
            command=self.exportar,
        ).grid(row=0, column=1, sticky="e")

        self.marco_tabla, self.tabla = tema.tabla_con_barra(
            cuerpo,
            (
                ("placa", "Placa", 130, "w"),
                ("entrada", "Entrada", 140, "w"),
                ("salida", "Salida", 140, "w"),
                ("permanencia", "Permanencia", 130, "w"),
                ("importe", "Importe", 110, "e"),
                ("operador", "Cobró", 120, "w"),
            ),
            altura=8,
        )
        self.marco_tabla.grid(row=1, column=0, sticky="nsew")

        self.vacio = ttk.Label(cuerpo, text="", style="Tenue.TLabel", anchor="center")

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

        if inicio > fin:
            # Se corrige en el mensaje y no en silencio: invertir el rango por
            # cuenta propia daría un reporte que el usuario no pidió.
            raise ValueError(
                "La fecha «Desde» es posterior a «Hasta». Invierta el orden."
            )

        zona = timezone.get_current_timezone()
        return (
            timezone.make_aware(datetime.combine(inicio, time.min), zona),
            timezone.make_aware(datetime.combine(fin, time.max), zona),
        )

    @operacion("Reporte")
    def generar(self) -> None:
        desde, hasta = self._leer_rango()
        datos = services_caja.reporte_por_rango(desde, hasta)

        self.m_total.actualizar(formato.moneda(datos["total_cobrado"]))
        self.m_folios.actualizar(str(datos["folios_cobrados"]))
        self.m_cortesias.actualizar(str(datos["folios_cortesia"]))
        self.m_promedio.actualizar(formato.moneda(datos["importe_promedio"]))
        self.m_permanencia.actualizar(
            formato.duracion(int(datos["minutos_promedio"]))
        )
        self.m_turnos.actualizar(str(datos["turnos_cerrados"]))

        self._folios = list(
            Folio.objects.select_related("operador_salida")
            .filter(
                estado=EstadoFolio.CERRADO, salida_en__gte=desde, salida_en__lte=hasta
            )
            .order_by("-salida_en")[:2000]
        )

        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        for indice, folio in enumerate(self._folios):
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
                tags=tema.etiquetas_de_fila(indice),
            )

        # Una tabla en blanco no distingue «no hubo movimientos» de «no cargó».
        if self._folios:
            self.vacio.grid_remove()
            self.marco_tabla.grid()
        else:
            self.marco_tabla.grid_remove()
            self.vacio.configure(
                text="No hay folios cobrados en el periodo seleccionado."
            )
            self.vacio.grid(row=1, column=0, sticky="nsew", pady=tema.ESPACIO_XL)

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

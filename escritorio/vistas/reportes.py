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

# Periodos de consulta habituales. «Hoy» y «Ayer» son días concretos; los
# demás son ventanas hacia atrás. Teclear fechas es donde se cometen los
# errores, así que lo que se pregunta a diario se resuelve con un botón.
ATAJOS = (
    ("Hoy", 0, 0),
    ("Ayer", 1, 1),
    ("Últimos 7 días", 6, 0),
    ("Últimos 30 días", 29, 0),
)


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
        tarjeta = tema.Tarjeta(self, "¿Qué periodo quiere ver?")
        tarjeta.grid(row=0, column=0, sticky="ew")
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        # --- Periodos de un clic --------------------------------------------
        #
        # Van primero, antes de los campos de fecha: cubren casi todas las
        # consultas reales, y quien solo quiere saber «cómo vamos hoy» no tiene
        # por qué escribir una fecha.
        atajos = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        atajos.grid(row=0, column=0, sticky="w")

        for columna, (texto, desde_dias, hasta_dias) in enumerate(ATAJOS):
            ttk.Button(
                atajos,
                text=texto,
                style="Secundario.TButton",
                command=(
                    lambda d=desde_dias, h=hasta_dias, n=texto: self._aplicar_atajo(d, h, n)
                ),
            ).grid(row=0, column=columna, padx=(0, tema.ESPACIO_S))

        # --- Fechas a mano, para el periodo que los atajos no cubren --------
        filtros = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        filtros.grid(row=1, column=0, sticky="w", pady=(tema.ESPACIO_M, 0))

        hoy = timezone.localtime().date()

        ttk.Label(filtros, text="o bien, del", style="Secundario.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, tema.ESPACIO_S)
        )
        self.desde = ttk.Entry(filtros, width=12)
        self.desde.grid(row=0, column=1, padx=(0, tema.ESPACIO_S))
        self.desde.insert(0, hoy.strftime(FORMATO_FECHA))
        self.desde.bind("<Return>", lambda _e: self.generar())

        ttk.Label(filtros, text="al", style="Secundario.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, tema.ESPACIO_S)
        )
        self.hasta = ttk.Entry(filtros, width=12)
        self.hasta.grid(row=0, column=3, padx=(0, tema.ESPACIO_M))
        self.hasta.insert(0, hoy.strftime(FORMATO_FECHA))
        self.hasta.bind("<Return>", lambda _e: self.generar())

        ttk.Button(
            filtros, text="Ver", style="Principal.TButton", command=self.generar
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(
            filtros,
            text=f"día/mes/año, por ejemplo {hoy.strftime(FORMATO_FECHA)}",
            style="Tenue.TLabel",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(tema.ESPACIO_XS, 0))

        ttk.Separator(cuerpo, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=tema.ESPACIO_L
        )

        # --- Qué se está mostrando ------------------------------------------
        #
        # Una pantalla de cifras sin el periodo a la vista invita a leer el
        # total de la semana pasada como el de hoy.
        self.titulo_periodo = ttk.Label(cuerpo, text="", style="Titulo.TLabel")
        self.titulo_periodo.grid(row=3, column=0, sticky="w", pady=(0, tema.ESPACIO_M))

        # --- Totales ---------------------------------------------------------
        metricas = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        metricas.grid(row=4, column=0, sticky="ew")

        self.m_total = tema.Metrica(metricas, "Dinero cobrado")
        self.m_folios = tema.Metrica(metricas, "Vehículos cobrados")
        self.m_cortesias = tema.Metrica(metricas, "Salidas sin cobro")
        self.m_promedio = tema.Metrica(metricas, "Cobro promedio")
        self.m_permanencia = tema.Metrica(metricas, "Tiempo promedio dentro")
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
        ttk.Label(
            encabezado,
            text="VEHÍCULOS COBRADOS EN EL PERIODO",
            style="Seccion.TLabel",
        ).grid(row=0, column=0, sticky="w")
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
                ("entrada", "Entró", 140, "w"),
                ("salida", "Salió", 140, "w"),
                ("permanencia", "Estuvo dentro", 130, "w"),
                ("importe", "Pagó", 110, "e"),
                ("operador", "Lo cobró", 120, "w"),
            ),
            altura=8,
        )
        self.marco_tabla.grid(row=1, column=0, sticky="nsew")

        self.vacio = ttk.Label(cuerpo, text="", style="Tenue.TLabel", anchor="center")

    # -------------------------------------------------------------- Acciones
    def _aplicar_atajo(self, desde_dias: int, hasta_dias: int, _nombre: str) -> None:
        """
        Rellena las fechas del atajo y genera el reporte.

        El atajo necesita principio **y** fin porque «Ayer» no es una ventana
        que llega hasta hoy: un reporte de ayer que incluyera lo de hoy
        contestaría una pregunta distinta de la que se hizo.
        """
        hoy = timezone.localtime().date()
        self.desde.delete(0, "end")
        self.desde.insert(0, (hoy - timedelta(days=desde_dias)).strftime(FORMATO_FECHA))
        self.hasta.delete(0, "end")
        self.hasta.insert(0, (hoy - timedelta(days=hasta_dias)).strftime(FORMATO_FECHA))
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

    @staticmethod
    def _titulo_del_periodo(desde, hasta) -> str:
        """
        Describe el periodo en palabras, deducido de las fechas mismas.

        Se calcula y no se recuerda del botón pulsado: así el título sigue
        siendo cierto cuando las fechas se teclean a mano, y no queda un «Hoy»
        encabezando las cifras del mes pasado.
        """
        hoy = timezone.localtime().date()
        inicio, fin = desde.date(), hasta.date()
        dias = (fin - inicio).days + 1

        if inicio == fin:
            if fin == hoy:
                return f"Hoy, {inicio.strftime(FORMATO_FECHA)}"
            if (hoy - fin).days == 1:
                return f"Ayer, {inicio.strftime(FORMATO_FECHA)}"
            return inicio.strftime(FORMATO_FECHA)

        rango = (
            f"Del {inicio.strftime(FORMATO_FECHA)} al {fin.strftime(FORMATO_FECHA)}"
        )
        return f"{rango}   ·   {dias} días"

    @operacion("Reporte")
    def generar(self) -> None:
        desde, hasta = self._leer_rango()
        datos = services_caja.reporte_por_rango(desde, hasta)

        self.titulo_periodo.configure(text=self._titulo_del_periodo(desde, hasta))

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
                text="No se cobró ningún vehículo en este periodo."
            )
            self.vacio.grid(row=1, column=0, sticky="nsew", pady=tema.ESPACIO_XL)

    @operacion("Exportación")
    def exportar(self) -> None:
        if not self._folios:
            messagebox.showinfo(
                "Exportación",
                "No hay nada que exportar: en este periodo no se cobró "
                "ningún vehículo.",
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
            f"Se exportaron {len(self._folios)} cobros a:\n{destino}",
            parent=self.winfo_toplevel(),
        )

    def al_entrar(self) -> None:
        self.generar()

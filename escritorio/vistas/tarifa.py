"""
Pestaña del cuadro tarifario.

Dos reglas de control gobiernan esta pantalla:

1. **Solo perfil administrativo.** El precio es el parámetro más sensible del
   sistema. Un operador de caja no debe poder alterarlo: sería la vía más
   simple de cobrar de menos y quedarse con la diferencia. Para el resto, la
   pantalla es de solo lectura.

2. **Las tarifas no se editan, se sustituyen.** Guardar un cambio **crea una
   tarifa nueva** y desactiva la anterior. Así queda registro de qué precio
   estaba vigente en cada fecha, que es exactamente lo que hay que demostrar
   ante una aclaración del cliente o una verificación de la autoridad. Editar
   en sitio reescribiría la historia de los cobros ya realizados.
"""
from __future__ import annotations

from decimal import Decimal
from tkinter import messagebox, ttk

from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.models import Tarifa

from .. import formato
from ..errores import operacion


class VistaTarifa(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=12)
        self.contexto = contexto
        self.solo_lectura = not getattr(contexto.operador, "is_staff", False)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._construir_vigente()
        self._construir_formulario()
        self._construir_historial()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_vigente(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Tarifa vigente ", padding=12)
        grupo.grid(row=0, column=0, sticky="ew")
        self.etiqueta_vigente = ttk.Label(
            grupo, text="", justify="left", font=("TkDefaultFont", 11)
        )
        self.etiqueta_vigente.grid(row=0, column=0, sticky="w")

    def _construir_formulario(self) -> None:
        titulo = (
            " Nueva tarifa "
            if not self.solo_lectura
            else " Nueva tarifa (requiere perfil administrativo) "
        )
        grupo = ttk.LabelFrame(self, text=titulo, padding=12)
        grupo.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        self.campos: dict[str, ttk.Entry] = {}
        definicion = (
            ("nombre", "Nombre:", "General"),
            ("cuota_hora", "Cuota por hora:", "30.00"),
            ("minutos_cortesia", "Minutos de cortesía:", "15"),
            ("fraccion_minutos", "Fracción de cobro (min):", "1"),
            ("cobro_minimo", "Cobro mínimo:", "0.00"),
            ("tope_diario", "Tope diario (vacío = sin tope):", "250.00"),
        )
        for fila, (clave, etiqueta, valor) in enumerate(definicion):
            ttk.Label(grupo, text=etiqueta).grid(row=fila, column=0, sticky="w", pady=3)
            campo = ttk.Entry(grupo, width=18)
            campo.grid(row=fila, column=1, sticky="w", padx=(8, 0), pady=3)
            campo.insert(0, valor)
            if self.solo_lectura:
                campo.configure(state="disabled")
            self.campos[clave] = campo

        ttk.Label(
            grupo,
            text=(
                "Fracción 1 = cobro por minuto real (proporcionalidad estricta,\n"
                "conforme al criterio de cobro proporcional de PROFECO)."
            ),
            justify="left",
            foreground="#555555",
        ).grid(row=len(definicion), column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.boton_guardar = ttk.Button(
            grupo, text="Crear tarifa y poner en vigencia", command=self.guardar
        )
        self.boton_guardar.grid(
            row=len(definicion) + 1, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )
        if self.solo_lectura:
            self.boton_guardar.configure(state="disabled")

    def _construir_historial(self) -> None:
        grupo = ttk.LabelFrame(self, text=" Histórico de tarifas ", padding=8)
        grupo.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        grupo.columnconfigure(0, weight=1)
        grupo.rowconfigure(0, weight=1)

        columnas = ("nombre", "cuota", "cortesia", "fraccion", "minimo", "tope", "vigencia", "activa")
        self.tabla = ttk.Treeview(grupo, columns=columnas, show="headings")
        for columna, titulo, ancho, anclaje in (
            ("nombre", "Nombre", 120, "w"),
            ("cuota", "Cuota/hora", 100, "e"),
            ("cortesia", "Cortesía", 80, "e"),
            ("fraccion", "Fracción", 80, "e"),
            ("minimo", "Mínimo", 90, "e"),
            ("tope", "Tope diario", 100, "e"),
            ("vigencia", "Vigente desde", 130, "w"),
            ("activa", "Activa", 70, "center"),
        ):
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=anclaje)
        self.tabla.grid(row=0, column=0, sticky="nsew")

        barra = ttk.Scrollbar(grupo, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=barra.set)
        barra.grid(row=0, column=1, sticky="ns")

    # -------------------------------------------------------------- Acciones
    @operacion("Tarifa")
    def guardar(self) -> None:
        nombre = self.campos["nombre"].get().strip()
        if not nombre:
            raise ValueError("Capture un nombre para la tarifa.")

        cuota = formato.a_decimal(self.campos["cuota_hora"].get(), campo="cuota por hora")
        minimo = formato.a_decimal(self.campos["cobro_minimo"].get(), campo="cobro mínimo")

        try:
            cortesia = int(self.campos["minutos_cortesia"].get().strip() or 0)
            fraccion = int(self.campos["fraccion_minutos"].get().strip() or 1)
        except ValueError as exc:
            raise ValueError(
                "Los minutos de cortesía y la fracción deben ser números enteros."
            ) from exc

        texto_tope = self.campos["tope_diario"].get().strip()
        tope = formato.a_decimal(texto_tope, campo="tope diario") if texto_tope else None

        # El modelo de dominio valida los rangos antes de persistir: una
        # fracción inválida produciría cobros erróneos de forma silenciosa.
        from apps.estacionamiento.domain.tarifas import ParametrosTarifa

        ParametrosTarifa(
            cuota_hora=cuota,
            minutos_cortesia=cortesia,
            fraccion_minutos=fraccion,
            cobro_minimo=minimo,
            tope_diario=tope,
        )

        if not messagebox.askyesno(
            "Nueva tarifa",
            f"Se creará la tarifa «{nombre}» con cuota de {formato.moneda(cuota)}/hora\n"
            "y entrará en vigencia de inmediato.\n\n"
            "La tarifa anterior se desactivará, pero se conserva en el histórico:\n"
            "los folios ya cobrados mantienen el precio con que se calcularon.\n\n"
            "¿Continúa?",
            parent=self.winfo_toplevel(),
        ):
            return

        ahora = timezone.now()
        # Nombre único por vigencia: permite repetir el nombre comercial
        # («General») a lo largo del tiempo sin colisionar en la base.
        nombre_unico = f"{nombre} ({ahora:%Y-%m-%d %H:%M})"

        Tarifa.objects.filter(activa=True).update(activa=False)
        Tarifa.objects.create(
            nombre=nombre_unico,
            cuota_hora=cuota,
            minutos_cortesia=cortesia,
            fraccion_minutos=fraccion,
            cobro_minimo=minimo,
            tope_diario=tope,
            activa=True,
            vigente_desde=ahora,
        )

        messagebox.showinfo(
            "Nueva tarifa",
            "La tarifa quedó registrada y vigente.",
            parent=self.winfo_toplevel(),
        )
        self.refrescar()

    # ------------------------------------------------------------- Refresco
    def refrescar(self) -> None:
        try:
            vigente = services.obtener_tarifa_vigente()
            self.etiqueta_vigente.configure(
                text=(
                    f"{vigente.nombre}\n"
                    f"Cuota por hora:      {formato.moneda(vigente.cuota_hora)}\n"
                    f"Cortesía:            {vigente.minutos_cortesia} min\n"
                    f"Fracción de cobro:   {vigente.fraccion_minutos} min\n"
                    f"Cobro mínimo:        {formato.moneda(vigente.cobro_minimo)}\n"
                    f"Tope diario:         {formato.moneda(vigente.tope_diario)}\n"
                    f"Vigente desde:       {formato.fecha_hora(vigente.vigente_desde)}"
                )
            )
        except Exception:
            # Sin tarifa vigente no se puede cobrar: el aviso debe ser
            # inequívoco y señalar la acción a tomar.
            self.etiqueta_vigente.configure(
                text=(
                    "⚠ NO HAY TARIFA VIGENTE.\n"
                    "No es posible registrar entradas ni cobrar salidas hasta "
                    "que se registre una tarifa."
                )
            )

        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        for tarifa in Tarifa.objects.order_by("-vigente_desde")[:100]:
            self.tabla.insert(
                "",
                "end",
                values=(
                    tarifa.nombre,
                    formato.moneda(tarifa.cuota_hora),
                    f"{tarifa.minutos_cortesia} min",
                    f"{tarifa.fraccion_minutos} min",
                    formato.moneda(tarifa.cobro_minimo),
                    formato.moneda(tarifa.tope_diario),
                    formato.fecha_hora(tarifa.vigente_desde),
                    "Sí" if tarifa.activa else "No",
                ),
            )

    def al_entrar(self) -> None:
        self.refrescar()

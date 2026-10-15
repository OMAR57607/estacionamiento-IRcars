"""
Pestaña del cuadro tarifario.

Dos reglas de control gobiernan esta pantalla:

1. **Solo perfil administrativo.** El precio es el parámetro más sensible del
   sistema. Un operador de caja no debe poder alterarlo: sería la vía más
   simple de cobrar de menos y quedarse con la diferencia. Para el resto, la
   pantalla es de solo lectura, y lo dice con todas sus letras en lugar de
   limitarse a deshabilitar los campos sin explicar por qué.

2. **Las tarifas no se editan, se sustituyen.** Guardar un cambio **crea una
   tarifa nueva** y desactiva la anterior. Así queda registro de qué precio
   estaba vigente en cada fecha, que es exactamente lo que hay que demostrar
   ante una aclaración del cliente o una verificación de la autoridad. Editar
   en sitio reescribiría la historia de los cobros ya realizados.

Cada parámetro lleva su explicación junto al campo. Son cinco cifras que se
tocan una vez al año y cuyo efecto no es evidente —«fracción de cobro» no
significa nada por sí solo—, así que la ayuda tiene que estar donde se captura
y no en un manual que nadie abrirá.
"""
from __future__ import annotations

from tkinter import messagebox, ttk

from django.utils import timezone

from apps.estacionamiento import services
from apps.estacionamiento.models import Tarifa

from .. import formato, tema
from ..errores import operacion

# Definición del formulario: clave, etiqueta, valor inicial y la explicación
# que acompaña al campo.
CAMPOS = (
    ("nombre", "Nombre", "General", "Cómo se identifica este cuadro tarifario."),
    ("cuota_hora", "Cuota por hora", "30.00", "Precio de una hora completa."),
    (
        "minutos_cortesia",
        "Minutos de cortesía",
        "15",
        "Tolerancia sin costo, para maniobras.",
    ),
    (
        "fraccion_minutos",
        "Fracción de cobro (min)",
        "1",
        "Bloque mínimo facturable. 1 = cobro por minuto real.",
    ),
    (
        "cobro_minimo",
        "Cobro mínimo",
        "0.00",
        "Piso del importe. No aplica dentro de la cortesía.",
    ),
    (
        "tope_diario",
        "Tope diario",
        "250.00",
        "Techo por cada día natural iniciado. Vacío = sin tope.",
    ),
)


class VistaTarifa(ttk.Frame):
    def __init__(self, padre, contexto):
        super().__init__(padre, padding=tema.ESPACIO_L, style="Fondo.TFrame")
        self.contexto = contexto
        self.solo_lectura = not getattr(contexto.operador, "is_staff", False)

        # Columnas iguales: con la izquierda más estrecha, el histórico perdía
        # las dos últimas columnas.
        self.columnconfigure(0, weight=1, uniform="tar")
        self.columnconfigure(1, weight=1, uniform="tar")
        self.rowconfigure(1, weight=1)

        self._construir_vigente()
        self._construir_formulario()
        self._construir_historial()
        self.refrescar()

    # ------------------------------------------------------------------ UI
    def _construir_vigente(self) -> None:
        tarjeta = tema.Tarjeta(self, "Tarifa vigente")
        tarjeta.grid(row=0, column=0, sticky="new", padx=(0, tema.ESPACIO_M))
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)

        self.nombre_vigente = ttk.Label(cuerpo, text="—", style="Titulo.TLabel")
        self.nombre_vigente.grid(row=0, column=0, sticky="w")

        # La cuota por hora es el número que el cliente pregunta y el que se
        # exhibe en el letrero; va en grande y el resto como desglose.
        self.cuota_vigente = ttk.Label(cuerpo, text="—", style="Cifra.TLabel")
        self.cuota_vigente.grid(row=1, column=0, sticky="w", pady=(tema.ESPACIO_S, 0))
        ttk.Label(cuerpo, text="por hora", style="Tenue.TLabel").grid(
            row=2, column=0, sticky="w"
        )

        ttk.Separator(cuerpo, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=tema.ESPACIO_M
        )

        self.detalle_vigente = ttk.Frame(cuerpo, style="Tarjeta.TFrame")
        self.detalle_vigente.grid(row=4, column=0, sticky="ew")
        self.detalle_vigente.columnconfigure(1, weight=1)

    def _construir_formulario(self) -> None:
        titulo = (
            "Nueva tarifa" if not self.solo_lectura else "Nueva tarifa (bloqueada)"
        )
        tarjeta = tema.Tarjeta(self, titulo)
        tarjeta.grid(row=0, column=1, rowspan=2, sticky="nsew")
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(1, weight=1)

        fila = 0
        if self.solo_lectura:
            # Un campo deshabilitado sin explicación se lee como un fallo del
            # programa. Decir por qué está bloqueado evita la llamada a soporte.
            aviso = ttk.Label(
                cuerpo,
                text=(
                    "Solo el personal administrativo puede modificar el cuadro "
                    "tarifario.\nEl precio no se cambia desde una caja."
                ),
                style="Secundario.TLabel",
                justify="left",
            )
            aviso.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, tema.ESPACIO_M))
            fila = 1

        self.campos: dict[str, ttk.Entry] = {}
        for clave, etiqueta, valor, ayuda in CAMPOS:
            ttk.Label(cuerpo, text=etiqueta, style="Secundario.TLabel").grid(
                row=fila, column=0, sticky="w", pady=(tema.ESPACIO_S, 0)
            )
            campo = ttk.Entry(cuerpo, width=16)
            campo.grid(
                row=fila, column=1, sticky="w", padx=(tema.ESPACIO_M, 0),
                pady=(tema.ESPACIO_S, 0),
            )
            campo.insert(0, valor)
            if self.solo_lectura:
                campo.configure(state="disabled")
            self.campos[clave] = campo

            ttk.Label(cuerpo, text=ayuda, style="Tenue.TLabel").grid(
                row=fila + 1, column=0, columnspan=2, sticky="w"
            )
            fila += 2

        ttk.Label(
            cuerpo,
            text=(
                "Guardar crea una tarifa nueva y desactiva la anterior; los folios "
                "ya cobrados conservan el precio con que se calcularon."
            ),
            style="Tenue.TLabel",
            wraplength=360,
            justify="left",
        ).grid(row=fila, column=0, columnspan=2, sticky="w", pady=(tema.ESPACIO_M, 0))

        self.boton_guardar = ttk.Button(
            cuerpo,
            text="Crear tarifa y poner en vigencia",
            style="Principal.TButton",
            command=self.guardar,
        )
        self.boton_guardar.grid(
            row=fila + 1, column=0, columnspan=2, sticky="ew", pady=(tema.ESPACIO_M, 0)
        )
        if self.solo_lectura:
            self.boton_guardar.configure(state="disabled")
        cuerpo.rowconfigure(fila + 2, weight=1)

    def _construir_historial(self) -> None:
        tarjeta = tema.Tarjeta(self, relleno=tema.ESPACIO_M)
        tarjeta.grid(
            row=1, column=0, sticky="nsew", padx=(0, tema.ESPACIO_M),
            pady=(tema.ESPACIO_M, 0),
        )
        cuerpo = tarjeta.cuerpo
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(1, weight=1)

        ttk.Label(cuerpo, text="HISTÓRICO DE TARIFAS", style="Seccion.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, tema.ESPACIO_S)
        )

        marco, self.tabla = tema.tabla_con_barra(
            cuerpo,
            (
                ("cuota", "Cuota/hora", 95, "e"),
                ("cortesia", "Cortesía", 80, "e"),
                ("fraccion", "Fracción", 80, "e"),
                ("tope", "Tope", 90, "e"),
                ("vigencia", "Vigente desde", 130, "w"),
                ("activa", "Activa", 70, "center"),
            ),
            altura=6,
        )
        marco.grid(row=1, column=0, sticky="nsew")

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
        for hijo in self.detalle_vigente.winfo_children():
            hijo.destroy()

        try:
            vigente = services.obtener_tarifa_vigente()
        except Exception:
            # Sin tarifa vigente no se puede cobrar: el aviso debe ser
            # inequívoco y señalar la acción a tomar.
            self.nombre_vigente.configure(
                text="No hay tarifa vigente", foreground=tema.PELIGRO
            )
            self.cuota_vigente.configure(text="—", foreground=tema.DESHABILITADO)
            ttk.Label(
                self.detalle_vigente,
                text=(
                    "No es posible registrar entradas ni cobrar salidas hasta "
                    "registrar una tarifa."
                ),
                style="Peligro.TLabel",
                wraplength=280,
                justify="left",
            ).grid(row=0, column=0, columnspan=2, sticky="w")
        else:
            self.nombre_vigente.configure(text=vigente.nombre, foreground=tema.TEXTO)
            self.cuota_vigente.configure(
                text=formato.moneda(vigente.cuota_hora), foreground=tema.TEXTO
            )
            renglones = (
                ("Cortesía", f"{vigente.minutos_cortesia} min"),
                ("Fracción de cobro", f"{vigente.fraccion_minutos} min"),
                ("Cobro mínimo", formato.moneda(vigente.cobro_minimo)),
                ("Tope diario", formato.moneda(vigente.tope_diario)),
                ("Vigente desde", formato.fecha_hora(vigente.vigente_desde)),
            )
            for fila, (etiqueta, valor) in enumerate(renglones):
                ttk.Label(
                    self.detalle_vigente, text=etiqueta, style="Secundario.TLabel"
                ).grid(row=fila, column=0, sticky="w", pady=2)
                ttk.Label(
                    self.detalle_vigente, text=valor, font=tema.fuente("cuerpo_fuerte")
                ).grid(row=fila, column=1, sticky="e", pady=2)

        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        for indice, tarifa in enumerate(Tarifa.objects.order_by("-vigente_desde")[:100]):
            self.tabla.insert(
                "",
                "end",
                values=(
                    formato.moneda(tarifa.cuota_hora),
                    f"{tarifa.minutos_cortesia} min",
                    f"{tarifa.fraccion_minutos} min",
                    formato.moneda(tarifa.tope_diario),
                    formato.fecha_hora(tarifa.vigente_desde),
                    "Sí" if tarifa.activa else "—",
                ),
                tags=tema.etiquetas_de_fila(indice),
            )

    def al_entrar(self) -> None:
        self.refrescar()

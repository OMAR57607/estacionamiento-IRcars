"""
Formularios de la interfaz web de caja.

Responsabilidad única: validar la forma de lo que se captura. Las reglas de
negocio —si la placa ya está dentro, si el pago alcanza, si hay turno abierto—
viven en los servicios y se aplican igual aquí que en la API. Duplicarlas en
el formulario garantizaría que algún día
divergieran.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from ..domain.corte import MINIMO_JUSTIFICACION, justificacion_suficiente
from ..models import TipoCajon, TipoVehiculo
from ..services_metricas import MINIMO_VISITAS, TOP_CLIENTES


class CapturaForm(forms.Form):
    """
    La captura única de la caja: una placa y el tipo de vehículo.

    Sustituye a los dos formularios que había, uno para la entrada y otro para
    la salida. En el teléfono quedaban apilados con el mismo campo de placa, y
    el operador tecleaba en el equivocado: recibía un error en lugar de la
    operación. Ahora hay un solo campo y dos botones, y el servidor decide qué
    hacer según el estado de esa placa —si está dentro, si es pensionado—.

    Los nombres de los campos (`placa`, `tipo_cajon`) se conservan: son el
    contrato con los envíos de la propia caja y con cualquier integración que
    ya los use.
    """

    placa = forms.CharField(
        max_length=15,
        label="Placa",
        error_messages={
            "required": "Teclee la placa que ve en el vehículo.",
            "max_length": "Una placa no tiene más de 15 caracteres.",
        },
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "off",
                # El teclado del móvil abre en mayúsculas y sin autocorrección:
                # una placa corregida automáticamente a una palabra es un error
                # difícil de notar y que luego impide encontrar el folio.
                "autocapitalize": "characters",
                "spellcheck": "false",
                "class": "campo campo-grande",
                "placeholder": "ABC-123",
                # Mejora progresiva: mayúsculas al teclear y filtro del patio.
                "data-mayusculas": "",
                "data-filtra": "#patio",
            }
        ),
    )
    tipo_cajon = forms.ChoiceField(
        # Rótulos cortos: cada opción es un botón de un tercio del ancho del
        # teléfono. El largo («personas con discapacidad») va en la ayuda.
        choices=[
            (TipoCajon.AUTOMOVIL, "Automóvil"),
            (TipoCajon.MOTOCICLETA, "Moto"),
            (TipoCajon.ACCESIBLE, "Accesible"),
        ],
        initial=TipoCajon.AUTOMOVIL,
        required=False,
        label="Tipo de vehículo",
        help_text=(
            "Decide el precio: la motocicleta tiene su propia tarifa. "
            "«Accesible» es el cajón reservado a personas con discapacidad y "
            "cobra como automóvil."
        ),
        # Radios y no un desplegable: la elección es de tres opciones, se hace
        # en cada entrada y decide el precio. Un toque, con las opciones a la
        # vista, en lugar de dos toques y un menú del sistema.
        widget=forms.RadioSelect,
    )

    def clean_placa(self):
        # Una placa hecha solo de espacios o guiones pasaría la validación de
        # «requerido» y llegaría vacía al servicio, que la rechazaría con un
        # mensaje menos claro que este.
        placa = self.cleaned_data["placa"]
        if not placa.replace(" ", "").replace("-", "").strip():
            raise forms.ValidationError("Teclee la placa que ve en el vehículo.")
        return placa

    def clean_tipo_cajon(self):
        # El campo es opcional en el formulario para simplificar la captura
        # cuando no se administran cajones; el valor por omisión es automóvil.
        return self.cleaned_data.get("tipo_cajon") or TipoCajon.AUTOMOVIL


class CobroForm(forms.Form):
    """
    Cobro de un folio ya cotizado.

    El folio viaja en un campo oculto en lugar de releerse de la placa: entre
    la cotización y el cobro podría registrarse otro vehículo con placa
    similar, y el cobro debe aplicarse exactamente al folio que se le mostró al
    cliente.
    """

    folio = forms.UUIDField(widget=forms.HiddenInput)
    pago_recibido = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        label="Pago recibido",
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "autofocus": True,
                "class": "campo campo-grande",
                "placeholder": "0.00",
                # inputmode decimal abre el teclado numérico en móvil.
                "inputmode": "decimal",
                # El cambio estimado se escribe aquí mientras se teclea.
                "data-cambio": "#cambio-estimado",
            }
        ),
        help_text="Déjelo vacío si no se recibe efectivo (cortesía o terminal).",
    )


class CancelarFolioForm(forms.Form):
    """Cancelación administrativa. El motivo es obligatorio por control interno."""

    folio = forms.UUIDField(widget=forms.HiddenInput)
    motivo = forms.CharField(
        max_length=500,
        label="Motivo de la cancelación",
        widget=forms.TextInput(
            attrs={"class": "campo", "placeholder": "Placa capturada por error"}
        ),
    )


class AbrirTurnoForm(forms.Form):
    """Apertura de caja con declaración del fondo inicial."""

    fondo_inicial = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        initial=Decimal("0.00"),
        label="Cambio con que abre la caja",
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "class": "campo campo-grande",
                "inputmode": "decimal",
            }
        ),
    )


class CerrarTurnoForm(forms.Form):
    """
    Cierre ciego.

    Solo pide el efectivo contado. La pantalla nunca muestra el total esperado
    antes de recibir esta cifra: si el operador lo conociera, el conteo se
    ajustaría a él y el faltante jamás se registraría.
    """

    efectivo_declarado = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        # El rótulo dice qué cifra va; el texto de ayuda, cómo contarla. Antes
        # todo era una etiqueta de dos renglones por encima del único campo
        # que hay que llenar.
        label="Efectivo contado",
        help_text="Todo el efectivo de la caja, también el cambio con que abrió.",
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "autofocus": True,
                "class": "campo campo-grande",
                "inputmode": "decimal",
            }
        ),
    )
    observaciones = forms.CharField(
        max_length=500,
        required=False,
        label="Observaciones (opcional)",
        widget=forms.TextInput(attrs={"class": "campo"}),
    )


class CerrarTurnoDeOtroForm(forms.Form):
    """
    Cierre, por el perfil administrativo, del turno de otro operador.

    Para cuando el cajero se fue sin cortar. Pide el efectivo que contó quien
    cierra —el conteo sigue siendo físico— y el motivo, obligatorio: un corte
    que no firmó su operador tiene que decir por qué. La contraseña se pide
    en el paso de confirmación, que es donde se decide.
    """

    efectivo_declarado = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Efectivo contado",
        help_text="Cuente usted todo el efectivo de esa caja, también el cambio con que abrió.",
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "autofocus": True,
                "class": "campo campo-grande",
                "inputmode": "decimal",
            }
        ),
    )
    motivo = forms.CharField(
        max_length=500,
        label="Por qué lo cierra usted",
        help_text="Quedará en el comprobante. Por ejemplo: el operador se fue sin cerrar.",
        widget=forms.Textarea(
            attrs={
                "class": "campo",
                "rows": 2,
                "placeholder": "El operador terminó su jornada y no cerró el turno.",
            }
        ),
    )

    def clean_motivo(self) -> str:
        # La misma regla que la justificación de un ajuste, y en el mismo
        # sitio: el dominio. El servicio la vuelve a aplicar.
        from ..domain.corte import justificacion_suficiente

        motivo = self.cleaned_data["motivo"].strip()
        if not justificacion_suficiente(motivo):
            raise forms.ValidationError(
                "Explique qué pasó con algo más que una palabra."
            )
        return motivo


class RangoFechasForm(forms.Form):
    """Periodo del reporte administrativo."""

    desde = forms.DateField(
        label="Desde",
        widget=forms.DateInput(attrs={"type": "date", "class": "campo"}),
    )
    hasta = forms.DateField(
        label="Hasta",
        widget=forms.DateInput(attrs={"type": "date", "class": "campo"}),
    )

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get("desde"), datos.get("hasta")
        if desde and hasta and hasta < desde:
            raise forms.ValidationError(
                "El rango está invertido: la fecha final es anterior a la inicial."
            )
        return datos

    @staticmethod
    def valores_por_omision() -> dict:
        from django.utils import timezone

        hoy = timezone.localtime().date()
        return {"desde": hoy, "hasta": hoy}


class ConfirmarClaveForm(forms.Form):
    """
    Confirmación de contraseña antes de una acción delicada.

    La usan el cambio de precio y el borrado de movimientos de prueba. Una
    copia por pantalla acabaría con que una pide la contraseña y la otra no.

    Se pide la contraseña del propio operador y no una clave aparte: una clave
    de supervisor acaba escrita en un papel pegado al monitor, que es peor que
    no tenerla. Así el cambio queda atribuido a quien se autenticó.
    """

    clave = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autofocus": True,
                "autocomplete": "current-password",
                "class": "campo campo-grande",
            }
        ),
    )


class TarifaForm(forms.Form):
    """
    Cambio del precio.

    Dos decisiones gobiernan este formulario:

    * **No existe formulario de edición a propósito.** Guardar crea un precio
      nuevo y desactiva el anterior, de modo que quede registro de qué precio
      estuvo vigente en cada fecha. Editar en sitio reescribiría la historia de
      los cobros ya realizados.

    * **Ningún campo trae valor de ejemplo.** Los `initial` los arma la vista a
      partir del precio vigente. Cuando estaban aquí, fijos en el código,
      «subir un peso la hora» reescribía en silencio la cortesía, la fracción y
      el tope con lo que decía esta clase.

    El nombre tampoco se pide: lo genera la vista con una marca de tiempo. El
    modelo lo exige único y quien cambia el precio es el dueño, que no tiene
    por qué bautizarlo.
    """

    cuota_hora = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Precio por hora",
        help_text="Lo que cuesta una hora completa. Es la cifra del letrero.",
        widget=forms.NumberInput(
            attrs={
                # Es la cifra que se viene a cambiar: el cursor empieza aquí.
                "autofocus": True,
                "step": "0.01",
                "class": "campo campo-grande",
                "inputmode": "decimal",
            }
        ),
    )
    minutos_cortesia = forms.IntegerField(
        min_value=0,
        label="Minutos gratis al entrar",
        help_text=(
            "Tolerancia sin costo, para maniobrar o dejar a alguien. "
            "0 = se cobra desde el primer minuto."
        ),
        widget=forms.NumberInput(attrs={"class": "campo", "inputmode": "numeric"}),
    )
    minutos_tolerancia = forms.IntegerField(
        min_value=0,
        max_value=59,
        label="Tolerancia por hora: después se cobra la hora completa (minutos)",
        help_text=(
            "Gracia sobre cada hora cumplida. Dentro de ella se cobra el tiempo "
            "transcurrido; al rebasarla, la hora completa. Ej. 15 con cuota de "
            "$15: a la 1:10 se cobra $17.50 y a la 1:16, $30.00. "
            "0 = sin salto, siempre proporcional."
        ),
        widget=forms.NumberInput(attrs={"class": "campo", "inputmode": "numeric"}),
    )
    fraccion_minutos = forms.IntegerField(
        min_value=1,
        max_value=60,
        label="Redondeo del tiempo cobrado (minutos)",
        help_text=(
            "No es la tolerancia: esto solo redondea el tiempo hacia arriba. "
            "1 = se paga el tiempo exacto, proporcionalidad estricta conforme al "
            "criterio de cobro proporcional de PROFECO. 15 = una estancia de 1:01 "
            "se cobra como 1:15."
        ),
        widget=forms.NumberInput(attrs={"class": "campo", "inputmode": "numeric"}),
    )
    cobro_minimo = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Cobro mínimo",
        help_text="Piso del importe, pasados los minutos gratis. 0 = sin mínimo.",
        widget=forms.NumberInput(
            attrs={"step": "0.01", "class": "campo", "inputmode": "decimal"}
        ),
    )
    tope_diario = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        label="Máximo por día",
        help_text=(
            "Más de esto no se cobra por cada día iniciado. "
            "Vacío = sin máximo."
        ),
        widget=forms.NumberInput(
            attrs={"step": "0.01", "class": "campo", "inputmode": "decimal"}
        ),
    )


class FormularioAcceso(AuthenticationForm):
    """
    Acceso que no distingue mayúsculas en el nombre de usuario.

    Los atributos del campo evitan que el teclado capitalice, pero no cubren
    todo: el operador puede escribir «Omar» por costumbre, un gestor de
    contraseñas puede guardarlo con otra caja, y en un teléfono nadie revisa
    la primera letra. El síntoma es el peor posible —«usuario o contraseña
    incorrectos» con la contraseña bien puesta— y lleva a creer que se perdió
    el acceso.

    **La contraseña sigue distinguiendo mayúsculas**: aquí no se relaja la
    autenticación, solo se resuelve a qué cuenta se refiere el nombre escrito.

    La resolución exige **una sola** coincidencia. Si hubiera dos cuentas que
    difieren únicamente en la caja de las letras, adivinar cuál se quería
    sería peor que fallar: se deja el valor tal cual y la autenticación sigue
    su curso normal.
    """

    def clean_username(self) -> str:
        escrito = self.cleaned_data.get("username", "")
        if not escrito:
            return escrito

        Usuario = get_user_model()
        coincidencias = list(
            Usuario.objects.filter(username__iexact=escrito).values_list(
                "username", flat=True
            )[:2]
        )
        if len(coincidencias) == 1:
            return coincidencias[0]
        return escrito


# --------------------------------------------------------------- Operadores


def _validar_nombre_disponible(nombre: str, excluyendo_pk=None) -> str:
    """
    Comprueba que el nombre no choque con otro **sin distinguir mayúsculas**.

    No es una preferencia estética. El acceso resuelve el nombre de usuario
    sin distinguir mayúsculas, para que el teclado de un teléfono no deje
    fuera al operador. Esa comodidad solo es segura mientras no existan dos
    cuentas que difieran únicamente en la caja de las letras: con «omar» y
    «Omar» a la vez, el acceso se vuelve ambiguo y, por diseño, deja de
    entrar a ninguna. Impedir el choque aquí es lo que sostiene aquello.
    """
    Usuario = get_user_model()
    colisiones = Usuario.objects.filter(username__iexact=nombre)
    if excluyendo_pk is not None:
        colisiones = colisiones.exclude(pk=excluyendo_pk)

    if colisiones.exists():
        existente = colisiones.first().username
        raise forms.ValidationError(
            f"Ya existe el operador «{existente}». Dos nombres que solo "
            "difieran en mayúsculas impedirían entrar a ambos."
        )
    return nombre


class _ClaveMixin:
    """Validación compartida de la contraseña nueva."""

    def clean(self):
        datos = super().clean()
        clave, repetida = datos.get("clave"), datos.get("clave_repetida")

        if clave and repetida and clave != repetida:
            raise forms.ValidationError("Las dos contraseñas no coinciden.")

        if clave:
            try:
                # La misma política que la aplicación exige en cualquier otro
                # cambio de contraseña. Una cuenta creada desde aquí no debe
                # poder saltársela.
                validate_password(clave)
            except DjangoValidationError as exc:
                raise forms.ValidationError(exc.messages) from exc

        return datos


class OperadorNuevoForm(_ClaveMixin, forms.Form):
    """Alta de un operador."""

    usuario = forms.CharField(
        max_length=150,
        label="Nombre de usuario",
        widget=forms.TextInput(
            attrs={
                "class": "campo",
                "autocapitalize": "none",
                "autocorrect": "off",
                "spellcheck": "false",
                "autocomplete": "off",
                "placeholder": "nombre.apellido",
            }
        ),
    )
    clave = forms.CharField(
        label="Contraseña inicial",
        widget=forms.PasswordInput(attrs={"class": "campo", "autocomplete": "new-password"}),
    )
    clave_repetida = forms.CharField(
        label="Repita la contraseña",
        widget=forms.PasswordInput(attrs={"class": "campo", "autocomplete": "new-password"}),
    )
    administrativo = forms.BooleanField(
        required=False,
        label="Puede ver reportes y cambiar el precio",
        help_text=(
            "Déjelo sin marcar para quien solo cobra. El precio es lo que más "
            "conviene restringir: cobrar de menos empieza por poder bajarlo."
        ),
    )

    def clean_usuario(self) -> str:
        nombre = self.cleaned_data["usuario"].strip()
        return _validar_nombre_disponible(nombre)


class OperadorEditarForm(forms.Form):
    """Cambio de nombre y de permisos de un operador existente."""

    usuario = forms.CharField(
        max_length=150,
        label="Nombre de usuario",
        widget=forms.TextInput(
            attrs={
                "class": "campo",
                "autocapitalize": "none",
                "autocorrect": "off",
                "spellcheck": "false",
            }
        ),
    )
    administrativo = forms.BooleanField(
        required=False, label="Puede ver reportes y cambiar el precio"
    )

    def __init__(self, *args, operador=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._operador = operador

    def clean_usuario(self) -> str:
        nombre = self.cleaned_data["usuario"].strip()
        pk = getattr(self._operador, "pk", None)
        return _validar_nombre_disponible(nombre, excluyendo_pk=pk)


class ClaveOperadorForm(_ClaveMixin, forms.Form):
    """
    Restablecimiento de la contraseña de otro operador.

    No pide la contraseña actual —quien la restablece es precisamente quien no
    la conoce— y por eso la pantalla deja constancia de para quién es el
    cambio antes de aplicarlo.
    """

    clave = forms.CharField(
        label="Contraseña nueva",
        widget=forms.PasswordInput(attrs={"class": "campo", "autocomplete": "new-password"}),
    )
    clave_repetida = forms.CharField(
        label="Repita la contraseña",
        widget=forms.PasswordInput(attrs={"class": "campo", "autocomplete": "new-password"}),
    )


class AjusteCorteForm(forms.Form):
    """
    Corrección de un corte ya cerrado.

    Pide la cifra **real** y no el monto del ajuste. Capturar «+140» obliga a
    quien corrige a hacer la resta mentalmente y a acertar con el signo, y un
    signo invertido convierte un faltante de $140 en uno de $280 sin que nada
    lo delate. Pidiendo el efectivo que había en caja, la aritmética la hace el
    sistema y el error deja de ser posible.

    El motivo se elige de una lista y la justificación se escribe. Las dos
    cosas, no una: la lista permite contar cuántas veces se repite el mismo
    problema —que es lo que revela un fallo de procedimiento— y el texto
    explica el caso concreto, que es lo que hace falta al auditarlo meses
    después.
    """

    efectivo_real = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Efectivo que realmente había en caja",
        help_text=(
            "Inclúyalo todo, también el cambio con que se abrió. Es la misma "
            "cifra que se pidió al cerrar, ahora bien contada."
        ),
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "autofocus": True,
                "class": "campo campo-grande",
                "inputmode": "decimal",
            }
        ),
    )
    motivo = forms.ChoiceField(
        label="Motivo",
        widget=forms.Select(attrs={"class": "campo"}),
    )
    justificacion = forms.CharField(
        label="Qué pasó",
        help_text=(
            "Con detalle suficiente para que se entienda dentro de seis meses "
            "sin preguntarle a nadie."
        ),
        widget=forms.Textarea(
            attrs={
                "class": "campo",
                "rows": 3,
                "placeholder": (
                    "Se localizó un cobro de $140 que no se registró; el "
                    "efectivo sí estaba en la caja."
                ),
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Las opciones se leen del modelo en tiempo de instancia y no se
        # copian aquí: una lista duplicada acabaría ofreciendo un motivo que
        # el servicio rechaza, y el operador vería un error sin explicación.
        from ..models import MotivoAjuste

        self.fields["motivo"].choices = MotivoAjuste.choices

    def clean_justificacion(self) -> str:
        texto = self.cleaned_data["justificacion"].strip()
        # La regla vive en el dominio y se aplica igual venga de donde venga.
        # Aquí se repite la comprobación solo para que el error salga junto al
        # campo en lugar de como un aviso suelto arriba de la pantalla.
        if not justificacion_suficiente(texto):
            raise forms.ValidationError(
                f"Explique qué pasó, con al menos {MINIMO_JUSTIFICACION} "
                "caracteres. Una palabra no permite auditar la corrección."
            )
        return texto


class MetricasForm(RangoFechasForm):
    """
    Periodo del reporte, más los parámetros de «cliente frecuente».

    Hereda el rango y sus validaciones en lugar de copiarlas: la comprobación
    de fechas invertidas debe comportarse igual en las dos pantallas.

    Los dos parámetros son visibles y editables a propósito. Qué cuenta como
    cliente frecuente depende de la longitud del periodo —dos visitas en un
    día es un dato notable y en un año es ruido— y dejarlo fijo en el código
    obligaría a interpretar la lista de memoria en cada reporte.
    """

    minimo_visitas = forms.IntegerField(
        min_value=2,
        max_value=100,
        required=False,
        # Con el campo en blanco, el operador no sabe con qué umbral se
        # calculó la lista que está mirando y tiene que deducirlo. El valor
        # por omisión se muestra escrito.
        initial=MINIMO_VISITAS,
        label="Visitas mínimas para considerar frecuente a un cliente",
        help_text="Desde 2. Con una sola visita no hay nada que distinguir.",
        widget=forms.NumberInput(attrs={"class": "campo", "inputmode": "numeric"}),
    )
    top_clientes = forms.IntegerField(
        min_value=1,
        max_value=100,
        required=False,
        initial=TOP_CLIENTES,
        label="Cuántos clientes listar",
        widget=forms.NumberInput(attrs={"class": "campo", "inputmode": "numeric"}),
    )

    @staticmethod
    def valores_por_omision() -> dict:
        """El periodo de hoy, con los parámetros de frecuencia por omisión."""
        return {
            **RangoFechasForm.valores_por_omision(),
            "minimo_visitas": MINIMO_VISITAS,
            "top_clientes": TOP_CLIENTES,
        }

    def clean_minimo_visitas(self) -> int:
        return self.cleaned_data.get("minimo_visitas") or MINIMO_VISITAS

    def clean_top_clientes(self) -> int:
        return self.cleaned_data.get("top_clientes") or TOP_CLIENTES


# --- Pensiones ---------------------------------------------------------------

# Días que se ofrecen como botón al cobrar. Cubren al que paga el día, el fin
# de semana, la semana y el mes; cualquier otra cantidad hasta el máximo del
# dominio se puede teclear.
DIAS_FRECUENTES = (1, 2, 3, 7, 15, 30)


class PensionForm(forms.Form):
    """
    Alta de una pensión. Una columna y pocas preguntas.

    El precio no se pregunta: sale del plan vigente. Que la caseta pudiera
    teclear el precio de cada pensión sería la vía más simple de cobrar de
    menos y quedarse con la diferencia.
    """

    placa = forms.CharField(
        max_length=15,
        label="Placa",
        error_messages={"required": "Teclee la placa del vehículo."},
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "class": "campo campo-grande",
                "placeholder": "ABC-123",
                "data-mayusculas": "",
            }
        ),
    )
    tipo_vehiculo = forms.ChoiceField(
        choices=TipoVehiculo.choices,
        initial=TipoVehiculo.AUTOMOVIL,
        label="Tipo de vehículo",
        help_text="Decide el tipo de cajón y la tarifa si se pasa de su vigencia.",
        widget=forms.RadioSelect,
    )
    nombre_cliente = forms.CharField(
        max_length=120,
        label="Nombre del cliente",
        error_messages={"required": "La pensión requiere el nombre del cliente."},
        widget=forms.TextInput(attrs={"class": "campo", "autocomplete": "off"}),
    )
    telefono = forms.CharField(
        max_length=20,
        required=False,
        label="Teléfono",
        widget=forms.TextInput(
            attrs={"class": "campo", "inputmode": "tel", "autocomplete": "off"}
        ),
    )
    descripcion_vehiculo = forms.CharField(
        max_length=80,
        required=False,
        label="Vehículo (opcional)",
        help_text="Marca, modelo y color: ayuda a reconocerlo en el patio.",
        widget=forms.TextInput(attrs={"class": "campo", "placeholder": "Jetta gris"}),
    )
    notas = forms.CharField(
        required=False,
        label="Notas (opcional)",
        widget=forms.Textarea(attrs={"class": "campo", "rows": 2}),
    )

    def clean_telefono(self):
        telefono = self.cleaned_data.get("telefono", "")
        digitos = "".join(c for c in telefono if c.isdigit())
        if telefono and not 8 <= len(digitos) <= 15:
            raise forms.ValidationError("Revise el teléfono: debe tener de 8 a 15 dígitos.")
        return digitos


class DatosPensionForm(forms.Form):
    """Corrección de los datos de contacto. La placa y el tipo no se editan."""

    nombre_cliente = PensionForm.base_fields["nombre_cliente"]
    telefono = PensionForm.base_fields["telefono"]
    descripcion_vehiculo = PensionForm.base_fields["descripcion_vehiculo"]
    notas = PensionForm.base_fields["notas"]
    clean_telefono = PensionForm.clean_telefono


class CobroPensionForm(forms.Form):
    """
    Cobro de días de pensión.

    El periodo y el monto no viajan en el formulario: los recalcula el
    servicio al cobrar. Lo único que decide quien cobra es cuántos días.
    """

    dias = forms.IntegerField(
        min_value=1,
        max_value=31,
        initial=1,
        label="Días a pagar",
        error_messages={
            "min_value": "Se paga al menos un día.",
            "max_value": "No se pueden pagar más de 31 días de una vez.",
        },
        widget=forms.HiddenInput,
    )
    pago_recibido = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        label="Pago recibido",
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "class": "campo campo-grande",
                "placeholder": "0.00",
                "inputmode": "decimal",
                "data-cambio": "#cambio-estimado",
            }
        ),
        help_text="Déjelo vacío si recibe el monto exacto.",
    )


class MotivoConClaveForm(forms.Form):
    """
    Baja de una pensión o cancelación de un pago: motivo escrito y contraseña.

    Las dos acciones mueven algo con efecto económico y no se deshacen. La
    contraseña convierte un toque de más en una decisión y deja la acción
    atribuida a quien se autenticó.
    """

    motivo = forms.CharField(
        max_length=500,
        label="Motivo",
        error_messages={"required": "Escriba el motivo."},
        widget=forms.TextInput(attrs={"class": "campo"}),
    )
    clave = forms.CharField(
        label="Su contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "campo", "autocomplete": "current-password"}),
    )


class PrecioPensionForm(forms.Form):
    precio_diario = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Precio por día",
        help_text="Lo que cuesta un día (24 horas) de pensión.",
        widget=forms.NumberInput(
            attrs={"step": "0.01", "class": "campo campo-grande", "inputmode": "decimal"}
        ),
    )

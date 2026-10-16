"""
Formularios de la interfaz web de caja.

Responsabilidad única: validar la forma de lo que se captura. Las reglas de
negocio —si la placa ya está dentro, si el pago alcanza, si hay turno abierto—
viven en los servicios y se aplican igual aquí que en la API o en el ejecutable
de escritorio. Duplicarlas en el formulario garantizaría que algún día
divergieran.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from ..models import TipoCajon


class EntradaForm(forms.Form):
    """Ingreso de un vehículo."""

    placa = forms.CharField(
        max_length=15,
        label="Placa",
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
            }
        ),
    )
    tipo_cajon = forms.ChoiceField(
        choices=TipoCajon.choices,
        initial=TipoCajon.AUTOMOVIL,
        required=False,
        label="Tipo",
        widget=forms.Select(attrs={"class": "campo"}),
    )

    def clean_tipo_cajon(self):
        # El campo es opcional en el formulario para simplificar la captura
        # cuando no se administran cajones; el valor por omisión es automóvil.
        return self.cleaned_data.get("tipo_cajon") or TipoCajon.AUTOMOVIL


class BuscarPlacaForm(forms.Form):
    """Búsqueda de la placa para cotizar la salida."""

    placa = forms.CharField(
        max_length=15,
        label="Placa",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "class": "campo campo-grande",
                "placeholder": "ABC-123",
            }
        ),
    )


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
        label="Fondo inicial (cambio en caja)",
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
        label="Efectivo contado en caja (incluye el fondo inicial)",
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
    def valores_por_omision() -> dict[str, date]:
        from django.utils import timezone

        hoy = timezone.localtime().date()
        return {"desde": hoy, "hasta": hoy}


class DesbloqueoPrecioForm(forms.Form):
    """
    Confirmación de contraseña antes de abrir el cambio de precio.

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

    Dos decisiones gobiernan este formulario, y son las mismas que gobiernan la
    pantalla equivalente del ejecutable de escritorio:

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
    fraccion_minutos = forms.IntegerField(
        min_value=1,
        max_value=60,
        label="Se cobra en bloques de (minutos)",
        help_text=(
            "1 = se paga el tiempo exacto, proporcionalidad estricta conforme al "
            "criterio de cobro proporcional de PROFECO. 15 = todo bloque de 15 "
            "minutos iniciado se cobra completo."
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

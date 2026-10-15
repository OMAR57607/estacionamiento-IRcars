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


class TarifaForm(forms.Form):
    """
    Alta de una tarifa nueva.

    No existe formulario de edición a propósito: guardar crea una tarifa y
    desactiva la anterior, de modo que quede registro de qué precio estuvo
    vigente en cada fecha. Editar en sitio reescribiría la historia de los
    cobros ya realizados.
    """

    nombre = forms.CharField(
        max_length=60,
        initial="General",
        label="Nombre",
        widget=forms.TextInput(attrs={"class": "campo"}),
    )
    cuota_hora = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Cuota por hora",
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "campo"}),
    )
    minutos_cortesia = forms.IntegerField(
        min_value=0,
        initial=15,
        label="Minutos de cortesía",
        widget=forms.NumberInput(attrs={"class": "campo"}),
    )
    fraccion_minutos = forms.IntegerField(
        min_value=1,
        max_value=60,
        initial=1,
        label="Fracción de cobro (minutos)",
        help_text=(
            "1 = cobro por minuto real (proporcionalidad estricta, conforme al "
            "criterio de cobro proporcional de PROFECO)."
        ),
        widget=forms.NumberInput(attrs={"class": "campo"}),
    )
    cobro_minimo = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        initial=Decimal("0.00"),
        label="Cobro mínimo",
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "campo"}),
    )
    tope_diario = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        label="Tope diario",
        help_text="Déjelo vacío para no aplicar tope.",
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "campo"}),
    )

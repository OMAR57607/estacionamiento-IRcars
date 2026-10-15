"""
Serializadores: frontera de entrada y salida de la API.

Responsabilidad única: validar la forma de los datos y presentarlos. Las
reglas de negocio no viven aquí, sino en `services.py` y en `domain/`.
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from .models import Cajon, Folio, Tarifa, TipoCajon


class TarifaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tarifa
        fields = [
            "id",
            "nombre",
            "cuota_hora",
            "minutos_cortesia",
            "fraccion_minutos",
            "cobro_minimo",
            "tope_diario",
            "activa",
            "vigente_desde",
            "creada_en",
        ]
        read_only_fields = ["id", "creada_en"]


class CajonSerializer(serializers.ModelSerializer):
    tipo_etiqueta = serializers.CharField(source="get_tipo_display", read_only=True)

    class Meta:
        model = Cajon
        fields = ["id", "codigo", "zona", "tipo", "tipo_etiqueta", "activo"]
        read_only_fields = ["id"]


class FolioSerializer(serializers.ModelSerializer):
    """Representación de lectura. Todos los campos calculados son de solo lectura."""

    cajon_codigo = serializers.CharField(source="cajon.codigo", read_only=True)
    cajon_tipo = serializers.CharField(source="cajon.tipo", read_only=True)
    tarifa_nombre = serializers.CharField(source="tarifa.nombre", read_only=True)
    estado_etiqueta = serializers.CharField(source="get_estado_display", read_only=True)

    class Meta:
        model = Folio
        fields = [
            "uuid",
            "placa",
            "cajon_codigo",
            "cajon_tipo",
            "tarifa_nombre",
            "estado",
            "estado_etiqueta",
            "entrada_en",
            "salida_en",
            "minutos",
            "importe",
            "pago_recibido",
            "cambio",
            "desglose",
            "observaciones",
            "creado_en",
        ]
        read_only_fields = fields


class EntradaSerializer(serializers.Serializer):
    """Datos de ingreso de un vehículo."""

    placa = serializers.CharField(max_length=15)
    tipo_cajon = serializers.ChoiceField(
        choices=TipoCajon.choices, default=TipoCajon.AUTOMOVIL
    )
    observaciones = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=500
    )


class SalidaSerializer(serializers.Serializer):
    """
    Datos de cobro.

    `pago_recibido` es opcional: permite registrar la salida sin efectivo
    (pensión, cortesía, pago con terminal). Cuando se envía, el servicio
    valida la suficiencia y calcula el cambio.
    """

    pago_recibido = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0.00"),
    )


class CancelacionSerializer(serializers.Serializer):
    """La cancelación exige un motivo: es un control interno, no un trámite."""

    motivo = serializers.CharField(max_length=500)

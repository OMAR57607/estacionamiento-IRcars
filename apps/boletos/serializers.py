"""
Validación de la ingesta de boletos.

Es la frontera del servicio público: todo lo que entra por aquí viene de fuera
y se trata como no confiable, aunque llegue con un token válido. El
serializador no solo comprueba tipos; **acota lo que puede escribirse**. Un
campo que no esté declarado aquí no llega a la base de datos, de modo que una
caja comprometida o una versión futura mal configurada no puede inyectar
atributos inesperados.
"""
from __future__ import annotations

from rest_framework import serializers

from .dominio import RELLENO
from .instantanea import VERSION_CONTRATO
from .models import BoletoPublicado, EstadoBoleto


class BoletoIngestaSerializer(serializers.ModelSerializer):
    """
    Contrato de entrada, espejo de `instantanea.instantanea_publica`.

    `version` se acepta y se descarta: existe para que el servicio pueda
    rechazar con un mensaje legible a una caja que hable un dialecto distinto,
    en lugar de fallar con un error de campo que no orienta a nadie.
    """

    version = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = BoletoPublicado
        fields = [
            "version",
            "uuid_folio",
            "placa_enmascarada",
            "estado",
            "ubicacion",
            "entrada_en",
            "salida_en",
            "minutos",
            "importe",
            "cuota_hora",
            "minutos_cortesia",
            "fraccion_minutos",
            "minutos_tolerancia",
            "cobro_minimo",
            "tope_diario",
        ]
        extra_kwargs = {
            # La unicidad se resuelve con un *upsert* en la vista, no
            # rechazando el reenvío: el publicador reintenta ante la duda y un
            # 400 por duplicado dejaría la bandeja atascada para siempre.
            "uuid_folio": {"validators": []},
        }

    def validate_version(self, valor: int) -> int:
        if valor > VERSION_CONTRATO:
            raise serializers.ValidationError(
                f"Contrato de la caja (v{valor}) más reciente que el del servicio "
                f"(v{VERSION_CONTRATO}). Actualice el servicio de boletos."
            )
        return valor

    def validate_estado(self, valor: str) -> str:
        if valor not in EstadoBoleto.values:
            raise serializers.ValidationError(f"Estado no reconocido: {valor}.")
        return valor

    def validate_placa_enmascarada(self, valor: str) -> str:
        """
        Último cerrojo del enmascarado.

        El enmascarado se aplica en la caja, que es donde debe aplicarse. Pero
        el servicio no tiene forma de auditar el código que corre en la caseta,
        y una versión antigua, una prueba mal configurada o una integración
        hecha a mano podrían enviar la placa completa. Rechazarla aquí
        garantiza que la base del servicio público no llegue a contener nunca
        una placa íntegra, sea cual sea el emisor.

        La comprobación es deliberadamente simple —exige al menos un carácter
        de relleno—, porque su propósito es atrapar el error honesto, no
        resistir a un emisor que quiera burlarla: quien controla la caja ya
        decide qué publica.
        """
        valor = (valor or "").strip()
        if not valor:
            raise serializers.ValidationError("La placa enmascarada es obligatoria.")
        if RELLENO not in valor:
            raise serializers.ValidationError(
                "La placa llega sin enmascarar. El servicio de boletos no almacena "
                "placas completas: enmascárela en el origen."
            )
        return valor

    def validate(self, datos: dict) -> dict:
        datos.pop("version", None)

        estado = datos.get("estado")
        salida = datos.get("salida_en")
        entrada = datos.get("entrada_en")

        if entrada and salida and salida < entrada:
            raise serializers.ValidationError(
                {"salida_en": "La salida no puede ser anterior a la entrada."}
            )
        if estado == EstadoBoleto.CERRADO and salida is None:
            raise serializers.ValidationError(
                {"salida_en": "Un boleto liquidado debe traer su hora de salida."}
            )
        return datos

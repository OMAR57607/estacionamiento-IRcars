"""
Elimina del servicio en la nube los boletos que ya cumplieron su función.

Esto no es mantenimiento opcional: es la aplicación del principio de
**temporalidad** de la Ley Federal de Protección de Datos Personales en
Posesión de los Particulares. Un dato personal —incluso enmascarado— no debe
conservarse más allá del tiempo necesario para la finalidad que justificó su
obtención, y la finalidad aquí es que el cliente consulte su boleto mientras
está en el estacionamiento y poco después.

El historial contable **no vive aquí**: vive en la caja, que es la fuente de
verdad. Borrar en la nube no pierde un solo folio ni afecta a ningún corte.

Se ejecuta desde el programador de tareas de la plataforma, típicamente a
diario.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.boletos.models import BoletoPublicado, EstadoBoleto

# Siete días cubre con holgura cualquier aclaración del cliente sobre un cobro
# reciente, que es el único motivo para conservar el boleto una vez liquidado.
DIAS_RETENCION = 7


class Command(BaseCommand):
    help = "Borra los boletos liquidados o cancelados que excedieron la retención."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dias",
            type=int,
            default=DIAS_RETENCION,
            help=f"Días de retención tras la salida (omisión: {DIAS_RETENCION}).",
        )
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Informa cuántos borraría, sin borrar nada.",
        )

    def handle(self, *args, **opciones):
        limite = timezone.now() - timedelta(days=opciones["dias"])

        # Solo lo que ya terminó su ciclo. Un boleto abierto no se toca por
        # antiguo que sea: el vehículo podría seguir dentro, y borrarlo
        # dejaría al cliente sin poder consultar su propio boleto.
        #
        # Las dos ramas del filtro no son redundancia defensiva: un boleto
        # **cancelado** no tiene hora de salida, porque el vehículo no salió
        # por caja. Filtrar solo por `salida_en` dejaría los cancelados en la
        # base para siempre, que es justo lo contrario de una política de
        # retención. Para esos se usa la fecha del último cambio recibido.
        candidatos = BoletoPublicado.objects.filter(
            Q(estado__in=[EstadoBoleto.CERRADO, EstadoBoleto.CANCELADO])
            & (
                Q(salida_en__lt=limite)
                | Q(salida_en__isnull=True, actualizado_en__lt=limite)
            )
        )
        total = candidatos.count()

        if opciones["simular"]:
            self.stdout.write(
                f"Se borrarían {total} boletos con salida anterior a "
                f"{timezone.localtime(limite):%Y-%m-%d %H:%M}."
            )
            return

        borrados, _ = candidatos.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Boletos depurados: {borrados} (de {total} candidatos).")
        )

"""
Siembra el precio de la pensión diaria: $180.00 por día.

Es el precio que fijó el dueño al confirmar el módulo (docs/REDISENO.md §2,
D1). Se siembra aquí y no se deja a que alguien lo capture porque, sin plan
vigente, la caseta no podría dar de alta ni cobrar la primera pensión, y el
fallo aparecería con el cliente delante.

Si ya existe un plan activo —una instalación que lo configuró a mano antes de
esta migración—, se respeta: sembrar encima desactivaría un precio que alguien
decidió.
"""
from decimal import Decimal

from django.db import migrations

PRECIO_DIARIO = Decimal("180.00")
NOMBRE = "Pensión diaria"


def sembrar_precio(apps, schema_editor):
    PlanPension = apps.get_model("estacionamiento", "PlanPension")
    if PlanPension.objects.filter(activo=True).exists():
        return
    PlanPension.objects.create(nombre=NOMBRE, precio_diario=PRECIO_DIARIO, activo=True)


def retirar_precio(apps, schema_editor):
    PlanPension = apps.get_model("estacionamiento", "PlanPension")
    # Solo el que sembró esta migración y solo si nunca se cobró con él.
    PlanPension.objects.filter(
        nombre=NOMBRE, precio_diario=PRECIO_DIARIO, pagos__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("estacionamiento", "0008_pensiones")]

    operations = [migrations.RunPython(sembrar_precio, retirar_precio)]

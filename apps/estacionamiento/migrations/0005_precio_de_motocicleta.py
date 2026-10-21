"""
Alta del precio de motocicleta, a petición del dueño: $10.00 por hora.

Se crea aquí y no a mano en la pantalla por una razón práctica: sin un precio
vigente para motocicleta, la primera entrada de una moto se detendría con un
error de configuración. El despliegue que introduce la distinción por tipo
debe dejar el sistema operable.

Los parámetros no se copian del precio de automóvil. El mínimo y el tope se
fijaron proporcionales —el mínimo equivale a una hora de moto; copiar los $15
del automóvil haría que la primera hora y media de una moto costara más que
una hora de auto— y la regla de cobro es la que el negocio definió: cobro al
minuto dentro de 15 minutos de tolerancia sobre cada hora cumplida, y hora
completa al rebasarla.
"""
from decimal import Decimal

from django.db import migrations
from django.utils import timezone

CUOTA_HORA = Decimal("10.00")
COBRO_MINIMO = Decimal("10.00")
TOPE_DIARIO = Decimal("170.00")
MINUTOS_TOLERANCIA = 15


def crear_precio_de_motocicleta(apps, schema_editor):
    Tarifa = apps.get_model("estacionamiento", "Tarifa")

    # Idempotente: si alguien ya capturó un precio de moto antes de que esta
    # migración corra —en un entorno de desarrollo, por ejemplo—, no se le
    # sobreescribe. Un precio es dinero: nunca se pisa en silencio.
    if Tarifa.objects.filter(tipo_vehiculo="MOTO").exists():
        return

    ahora = timezone.now()
    Tarifa.objects.create(
        nombre=f"Motocicleta {ahora:%Y-%m-%d %H:%M:%S}",
        tipo_vehiculo="MOTO",
        cuota_hora=CUOTA_HORA,
        minutos_cortesia=0,
        fraccion_minutos=1,
        minutos_tolerancia=MINUTOS_TOLERANCIA,
        cobro_minimo=COBRO_MINIMO,
        tope_diario=TOPE_DIARIO,
        activa=True,
        vigente_desde=ahora,
    )


def retirar_precio_de_motocicleta(apps, schema_editor):
    """
    Revertir no borra cobros: desactiva el precio y lo deja en el histórico.

    Un `delete` fallaría de todos modos contra los folios que lo referencian
    —la clave foránea es `PROTECT`—, y debe fallar: un folio sin su precio no
    puede explicar su propio importe.
    """
    Tarifa = apps.get_model("estacionamiento", "Tarifa")
    Tarifa.objects.filter(tipo_vehiculo="MOTO").update(activa=False)


class Migration(migrations.Migration):
    dependencies = [
        ("estacionamiento", "0004_tarifa_tipo_vehiculo_and_more"),
    ]

    operations = [
        migrations.RunPython(
            crear_precio_de_motocicleta, retirar_precio_de_motocicleta
        ),
    ]

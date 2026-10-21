"""
Corrige la regla de cobro del precio de automóvil, a petición del dueño.

El precio quedó capturado con «redondeo de 15 minutos» donde debía llevar
«15 minutos de tolerancia». Las dos capturas son válidas y el motor cobró
exactamente lo que se le pidió, pero cobran cosas distintas: con cuota de
$15, una estancia de 1 h 16 min cobraba $22.50 en lugar de los $30.00 que el
negocio quiere cobrar.

**No se edita el precio en sitio.** Se crea uno nuevo y se desactiva el
anterior, que es como funciona todo cambio de precio en este sistema: el
histórico es lo que permite demostrar qué precio estuvo exhibido en cada
fecha ante una aclaración o una verificación. Los folios ya cobrados
conservan su copia, y los vehículos que estén dentro en el momento del
despliegue siguen con la regla que se les exhibió al entrar, porque su folio
apunta al precio anterior. Eso es lo correcto: se aplica el precio vigente al
ingresar, no el del momento del cobro.

Los demás parámetros —cuota, cortesía, mínimo y tope— se **leen del precio
vigente**, no se escriben aquí. Esta migración corrige una regla mal
capturada; no es el lugar para fijar importes.
"""
from django.db import migrations
from django.utils import timezone

FRACCION_MINUTOS = 1
MINUTOS_TOLERANCIA = 15


def corregir_regla_de_automovil(apps, schema_editor):
    Tarifa = apps.get_model("estacionamiento", "Tarifa")

    vigente = (
        Tarifa.objects.filter(activa=True, tipo_vehiculo="AUTO")
        .order_by("-vigente_desde")
        .first()
    )

    # Sin precio de automóvil no hay nada que corregir: la caseta lo capturará
    # en la pantalla de Precio, ya con la regla correcta por omisión.
    if vigente is None:
        return

    # Idempotente: si alguien ya lo corrigió a mano desde la pantalla, no se
    # crea un precio nuevo idéntico al vigente. Un cambio de precio sin
    # cambio real solo ensucia el histórico que sirve para demostrar.
    if (
        vigente.fraccion_minutos == FRACCION_MINUTOS
        and vigente.minutos_tolerancia == MINUTOS_TOLERANCIA
    ):
        return

    ahora = timezone.now()
    vigente.activa = False
    vigente.save(update_fields=["activa"])

    Tarifa.objects.create(
        nombre=f"Automóvil {ahora:%Y-%m-%d %H:%M:%S}",
        tipo_vehiculo="AUTO",
        # Se conserva todo lo que es decisión de precio...
        cuota_hora=vigente.cuota_hora,
        minutos_cortesia=vigente.minutos_cortesia,
        cobro_minimo=vigente.cobro_minimo,
        tope_diario=vigente.tope_diario,
        # ...y se corrige solo la regla de cobro.
        fraccion_minutos=FRACCION_MINUTOS,
        minutos_tolerancia=MINUTOS_TOLERANCIA,
        activa=True,
        vigente_desde=ahora,
    )


def sin_vuelta_atras(apps, schema_editor):
    """
    Revertir no restaura el precio anterior, y no debe hacerlo.

    Un precio es un hecho con fecha: el anterior sigue en el histórico con su
    periodo de vigencia, y reactivarlo desde aquí fabricaría un tercer tramo
    que nunca estuvo exhibido. Si hiciera falta volver atrás, se captura de
    nuevo desde la pantalla de Precio, con contraseña y a nombre de quien lo
    decide.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("estacionamiento", "0005_precio_de_motocicleta"),
    ]

    operations = [
        migrations.RunPython(corregir_regla_de_automovil, sin_vuelta_atras),
    ]

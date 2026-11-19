"""
Permite corregir el cambio de apertura de un corte ya cerrado.

La corrección que existía (`0007`) solo enmienda lo **contado**. Un turno se
abrió declarando $9,000 de cambio que nunca existieron y no había forma de
corregirlo: «Debería haber en caja» quedó inflado en esa cifra, y cuadrarlo
con el ajuste disponible obligaba a declarar como contado un efectivo que
tampoco existió, que es justo lo que después inflaría el reporte del mes.

**Esta migración no modifica ni un dato existente.** Añade:

* `Turno.fondo_ajustado` y `Turno.esperado_ajustado`: el cambio real y el
  esperado que resulta de él. `fondo_inicial` y `efectivo_esperado` conservan
  para siempre lo declarado al abrir y lo que de ahí salió. `NULL` significa
  «nunca se corrigió».
* `AjusteCorte.fondo_anterior` y `AjusteCorte.fondo_nuevo`: el asiento de esa
  corrección, en la misma bitácora que los ajustes de lo contado.
* El motivo `FONDO`, que pone el sistema en ese asiento.

La restricción `ck_ajuste_monto_no_cero` se sustituye por
`ck_ajuste_mueve_algo`: un asiento sigue sin poder ser nulo, pero ahora
«mover algo» es cambiar lo contado **o** cambiar el cambio de apertura. Todos
los ajustes existentes cumplen la nueva, porque cumplían la anterior.
"""
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("estacionamiento", "0010_turno_cerrado_por"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="ajustecorte",
            name="ck_ajuste_monto_no_cero",
        ),
        migrations.AddField(
            model_name="ajustecorte",
            name="fondo_anterior",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                verbose_name="cambio de apertura antes de la corrección",
            ),
        ),
        migrations.AddField(
            model_name="ajustecorte",
            name="fondo_nuevo",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                verbose_name="cambio de apertura real",
            ),
        ),
        migrations.AddField(
            model_name="turno",
            name="esperado_ajustado",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Efectivo esperado tras corregir el cambio de apertura.",
                max_digits=12,
                null=True,
                verbose_name="efectivo esperado corregido",
            ),
        ),
        migrations.AddField(
            model_name="turno",
            name="fondo_ajustado",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Cambio con que realmente abrió la caja. Vacío si el declarado al abrir nunca se corrigió.",
                max_digits=12,
                null=True,
                verbose_name="cambio de apertura corregido",
            ),
        ),
        migrations.AlterField(
            model_name="ajustecorte",
            name="motivo",
            field=models.CharField(
                choices=[
                    ("RECUENTO", "Error de conteo: se recontó el efectivo"),
                    ("LOCALIZADO", "Efectivo localizado después del cierre"),
                    ("NO_REGISTRADO", "Cobro no registrado en el sistema"),
                    ("CAPTURA", "Error al teclear el efectivo declarado"),
                    ("RETIRO", "Retiro de efectivo no descontado"),
                    ("OTRO", "Otro (se detalla en la justificación)"),
                    ("FONDO", "Cambio de apertura mal declarado"),
                ],
                max_length=20,
                verbose_name="motivo",
            ),
        ),
        migrations.AddConstraint(
            model_name="ajustecorte",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("monto", Decimal("0.00")), _negated=True),
                    models.Q(
                        ("fondo_anterior__isnull", False),
                        ("fondo_nuevo__isnull", False),
                        models.Q(
                            ("fondo_anterior", models.F("fondo_nuevo")), _negated=True
                        ),
                    ),
                    _connector="OR",
                ),
                name="ck_ajuste_mueve_algo",
            ),
        ),
    ]

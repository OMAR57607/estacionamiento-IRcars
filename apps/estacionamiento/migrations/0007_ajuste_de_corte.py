"""
Permite corregir un corte de caja ya firmado, sin reescribirlo.

Un turno se cerró declarando $140 menos de los que había en caja y el sistema
no ofrecía forma de enmendarlo: el corte cerrado es inmutable por diseño y
`calcular_resumen` devuelve su fotografía en lugar de recalcularla. Esa
inmutabilidad es correcta —un corte firmado no debe cambiar de cifra— y aquí
no se toca. Lo que faltaba era la otra mitad del control: qué hacer cuando la
cifra declarada estaba mal.

Dejarla mal no era neutral. Un faltante inexistente en el histórico vuelve la
diferencia del corte una cifra en la que no se puede confiar, y con ella se
cae el único control real sobre la caja. La alternativa —editar la base a
mano— corrige el número y borra al responsable.

**Esta migración no modifica ni un dato existente.** Añade:

* `AjusteCorte`, la bitácora: de qué cifra se partió, a cuál se llegó, el
  motivo, la justificación escrita y quién la autorizó. Dos restricciones de
  base la sostienen: un ajuste no puede valer cero —no corregiría nada— ni
  quedarse sin justificación.
* `Turno.efectivo_ajustado` y `Turno.diferencia_ajustada`, el efecto
  acumulado. Son redundantes respecto a la bitácora a propósito, igual que los
  campos del corte son redundantes respecto a los folios: el reporte de un mes
  recorre miles de turnos y resolver su diferencia real recorriendo ajustes
  convertiría una consulta agregada en un recorrido en memoria. `NULL`
  significa «nunca se ajustó», que no es lo mismo que «se ajustó a cero».

Los campos del cierre —`efectivo_declarado` y `diferencia`— quedan intactos
para siempre. El comprobante muestra las dos cifras, y es esa coexistencia la
que hace del ajuste un acto auditable en lugar de un borrado.
"""
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("estacionamiento", "0006_corrige_la_regla_del_precio_de_automovil"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="turno",
            name="diferencia_ajustada",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Diferencia resultante del último ajuste aplicado.",
                max_digits=12,
                null=True,
                verbose_name="diferencia corregida",
            ),
        ),
        migrations.AddField(
            model_name="turno",
            name="efectivo_ajustado",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Efectivo real tras la corrección administrativa. Vacío si el corte nunca se ajustó.",
                max_digits=12,
                null=True,
                verbose_name="efectivo corregido",
            ),
        ),
        migrations.CreateModel(
            name="AjusteCorte",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "efectivo_esperado",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Copia de lo que el sistema esperaba. Redundante a propósito: el asiento debe explicar su propio monto sin depender del turno.",
                        max_digits=12,
                        verbose_name="efectivo esperado",
                    ),
                ),
                (
                    "efectivo_anterior",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        verbose_name="efectivo declarado antes del ajuste",
                    ),
                ),
                (
                    "efectivo_nuevo",
                    models.DecimalField(
                        decimal_places=2, max_digits=12, verbose_name="efectivo real"
                    ),
                ),
                (
                    "monto",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Positivo = apareció efectivo; negativo = había menos.",
                        max_digits=12,
                        verbose_name="monto del ajuste",
                    ),
                ),
                (
                    "diferencia_anterior",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        verbose_name="diferencia antes del ajuste",
                    ),
                ),
                (
                    "diferencia_nueva",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        verbose_name="diferencia después del ajuste",
                    ),
                ),
                (
                    "motivo",
                    models.CharField(
                        choices=[
                            ("RECUENTO", "Error de conteo: se recontó el efectivo"),
                            ("LOCALIZADO", "Efectivo localizado después del cierre"),
                            ("NO_REGISTRADO", "Cobro no registrado en el sistema"),
                            ("CAPTURA", "Error al teclear el efectivo declarado"),
                            ("RETIRO", "Retiro de efectivo no descontado"),
                            ("OTRO", "Otro (se detalla en la justificación)"),
                        ],
                        max_length=20,
                        verbose_name="motivo",
                    ),
                ),
                (
                    "justificacion",
                    models.TextField(
                        help_text="Qué pasó, con detalle suficiente para que se entienda meses después sin preguntar a nadie.",
                        verbose_name="justificación",
                    ),
                ),
                (
                    "creado_en",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="registrado en"
                    ),
                ),
                (
                    "autorizo",
                    models.ForeignKey(
                        help_text="Perfil administrativo que respondió por la corrección.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ajustes_autorizados",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="autorizó",
                    ),
                ),
                (
                    "turno",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ajustes",
                        to="estacionamiento.turno",
                        verbose_name="turno corregido",
                    ),
                ),
            ],
            options={
                "verbose_name": "ajuste de corte",
                "verbose_name_plural": "ajustes de corte",
                "ordering": ["-creado_en"],
                "indexes": [
                    models.Index(
                        fields=["turno", "creado_en"], name="idx_ajuste_turno_fecha"
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("monto", Decimal("0.00")), _negated=True),
                        name="ck_ajuste_monto_no_cero",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("justificacion", ""), _negated=True),
                        name="ck_ajuste_justificado",
                    ),
                ],
            },
        ),
    ]

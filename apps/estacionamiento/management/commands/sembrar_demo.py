"""
Comando de siembra para desarrollo y pruebas de aceptación.

Deja el sistema operable de inmediato: una tarifa vigente, un inventario de
cajones y un usuario operador. Es idempotente —puede ejecutarse varias veces
sin duplicar datos— para que pueda usarse sin temor en un entorno local.

Uso:
    python manage.py sembrar_demo
    python manage.py sembrar_demo --automoviles 40 --motocicletas 10
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.estacionamiento.models import Cajon, Tarifa, TipoCajon


class Command(BaseCommand):
    help = "Siembra una tarifa vigente, cajones y un usuario operador de prueba."

    def add_arguments(self, parser):
        parser.add_argument("--automoviles", type=int, default=20)
        parser.add_argument("--motocicletas", type=int, default=5)
        parser.add_argument("--accesibles", type=int, default=2)
        parser.add_argument("--cuota-hora", type=Decimal, default=Decimal("30.00"))
        parser.add_argument("--cortesia", type=int, default=15)
        parser.add_argument("--tope-diario", type=Decimal, default=Decimal("250.00"))

    @transaction.atomic
    def handle(self, *args, **opciones):
        # --- Tarifa ---------------------------------------------------------
        # La vigencia se fija un día atrás para que ampare cualquier folio que
        # se registre de inmediato en las pruebas.
        tarifa, creada = Tarifa.objects.get_or_create(
            nombre="General",
            defaults={
                "cuota_hora": opciones["cuota_hora"],
                "minutos_cortesia": opciones["cortesia"],
                "fraccion_minutos": 1,  # cobro proporcional por minuto real
                "cobro_minimo": Decimal("0.00"),
                "tope_diario": opciones["tope_diario"],
                "activa": True,
                "vigente_desde": timezone.now() - timezone.timedelta(days=1),
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f"Tarifa {'creada' if creada else 'existente'}: {tarifa}")
        )

        # --- Inventario de cajones -----------------------------------------
        plan = [
            ("A", TipoCajon.AUTOMOVIL, opciones["automoviles"]),
            ("M", TipoCajon.MOTOCICLETA, opciones["motocicletas"]),
            ("D", TipoCajon.ACCESIBLE, opciones["accesibles"]),
        ]
        for prefijo, tipo, cantidad in plan:
            nuevos = 0
            for n in range(1, cantidad + 1):
                _, creado = Cajon.objects.get_or_create(
                    codigo=f"{prefijo}-{n:02d}",
                    defaults={"tipo": tipo, "zona": "Planta baja"},
                )
                nuevos += int(creado)
            self.stdout.write(f"  {tipo}: {cantidad} cajones ({nuevos} nuevos)")

        # --- Usuario operador ----------------------------------------------
        # Credencial de desarrollo únicamente. En el equipo instalado en sitio
        # cada operador debe tener su propio usuario con contraseña propia:
        # la trazabilidad del cobro depende de ello.
        Usuario = get_user_model()
        operador, creado = Usuario.objects.get_or_create(
            username="operador",
            defaults={"is_staff": False, "is_superuser": False},
        )
        if creado:
            operador.set_password("operador-demo-1234")
            operador.save()
            self.stdout.write(
                self.style.WARNING(
                    "Usuario 'operador' creado con contraseña 'operador-demo-1234'. "
                    "Cámbiela antes de usar el sistema en operación real."
                )
            )
        else:
            self.stdout.write("Usuario 'operador' ya existía.")

        self.stdout.write(self.style.SUCCESS("Siembra completada."))

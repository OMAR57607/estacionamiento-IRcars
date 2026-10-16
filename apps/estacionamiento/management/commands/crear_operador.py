"""
Crea el usuario operador de la caja si todavía no existe.

Hace falta porque el panel de administración de Django no se monta: no hay
dónde dar de alta al primer usuario, y sin usuario no se puede entrar a la
caja. En un despliegue en la nube tampoco hay una terminal a mano donde
ejecutar `createsuperuser` de forma interactiva.

Dos decisiones que importan:

* **La contraseña se lee del entorno, no de un argumento.** Los argumentos de
  la línea de comandos son visibles en la lista de procesos del contenedor y
  quedan en el historial del intérprete de órdenes. Una variable de entorno no
  resuelve todos los problemas, pero sí esos dos.

* **Nunca cambia la contraseña de un usuario existente.** El comando se
  ejecuta en cada arranque, y restablecer la contraseña en cada despliegue
  dejaría la cuenta atada para siempre al valor de una variable: quien
  cambiara su contraseña desde la aplicación la vería revertida al siguiente
  despliegue, sin explicación.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

VARIABLE_USUARIO = "IRCARS_USUARIO_OPERADOR"
VARIABLE_CLAVE = "IRCARS_CLAVE_OPERADOR"


class Command(BaseCommand):
    help = "Da de alta al operador de la caja si no existe. Idempotente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario",
            default=None,
            help=f"Nombre de usuario. Por omisión, ${VARIABLE_USUARIO}.",
        )
        parser.add_argument(
            "--operativo",
            action="store_true",
            help=(
                "Crea la cuenta sin acceso a las pantallas administrativas "
                "(reportes y cambio de precio). Por omisión las incluye, "
                "porque el primer usuario tiene que poder fijar el precio."
            ),
        )

    def handle(self, *args, **opciones):
        usuario = opciones["usuario"] or os.environ.get(VARIABLE_USUARIO, "").strip()
        clave = os.environ.get(VARIABLE_CLAVE, "")

        if not usuario:
            raise CommandError(
                f"Indique el usuario con --usuario o la variable {VARIABLE_USUARIO}."
            )

        Usuario = get_user_model()
        existente = Usuario.objects.filter(username=usuario).first()
        if existente is not None:
            # Silencio deliberado y salida correcta: en cada arranque se
            # invoca este comando, y el caso normal es que la cuenta ya esté.
            self.stdout.write(f"El operador «{usuario}» ya existe. Sin cambios.")
            return

        if not clave:
            raise CommandError(
                f"Defina {VARIABLE_CLAVE} para crear al operador «{usuario}». "
                "No se crea una cuenta sin contraseña: quedaría inaccesible y "
                "habría que borrarla a mano."
            )

        try:
            # Se aplican los mismos validadores que la aplicación exige en
            # cualquier otro cambio de contraseña. Una cuenta creada por
            # arranque no debe poder saltarse la política.
            validate_password(clave)
        except ValidationError as exc:
            raise CommandError(
                "La contraseña no cumple la política: " + " ".join(exc.messages)
            ) from exc

        Usuario.objects.create_user(
            username=usuario,
            password=clave,
            # `is_staff` da acceso a reportes y al cambio de precio dentro de
            # la propia aplicación. No abre el panel de administración de
            # Django, que no está montado.
            is_staff=not opciones["operativo"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Operador «{usuario}» creado. "
                "Cambie la contraseña desde la aplicación y retire "
                f"{VARIABLE_CLAVE} del entorno."
            )
        )

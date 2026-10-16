"""
Drena la bandeja de salida de boletos desde la línea de comandos.

El ejecutable de caja ya publica por su cuenta en un hilo de fondo, de modo
que este comando no es la vía normal. Existe para tres situaciones reales:

* **Diagnóstico.** Mostrar en pantalla por qué no se publica, con el mensaje
  de error completo, sin tener que abrir la bitácora.
* **Puesta al día.** Tras un corte largo de internet, forzar el envío de todo
  lo acumulado sin esperar la cadencia del hilo.
* **Instalaciones sin interfaz.** Un equipo que use la API en lugar de la
  ventana puede invocarlo desde el programador de tareas.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.boletos.models import PublicacionPendiente
from apps.boletos.publicador import drenar, publicacion_activa, url_ingesta


class Command(BaseCommand):
    help = "Envía al servicio de boletos las publicaciones pendientes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lote",
            type=int,
            default=100,
            help="Máximo de publicaciones a enviar en esta ejecución.",
        )
        parser.add_argument(
            "--solo-listar",
            action="store_true",
            help="Muestra lo pendiente sin enviar nada.",
        )

    def handle(self, *args, **opciones):
        pendientes = PublicacionPendiente.objects.count()

        if opciones["solo_listar"]:
            self.stdout.write(f"Publicaciones pendientes: {pendientes}")
            for fila in PublicacionPendiente.objects.all()[:50]:
                motivo = fila.ultimo_error or "sin intentos fallidos"
                self.stdout.write(
                    f"  {fila.uuid_folio} · intentos={fila.intentos} · {motivo}"
                )
            return

        if not publicacion_activa():
            raise CommandError(
                "La publicación de boletos está desactivada o incompleta. "
                "Revise que estén definidos el origen del servicio y el token, "
                "y que la publicación esté marcada como activa."
            )

        self.stdout.write(f"Destino: {url_ingesta()}")
        self.stdout.write(f"Pendientes antes de enviar: {pendientes}")

        enviadas, fallidas = drenar(limite=opciones["lote"])

        self.stdout.write(self.style.SUCCESS(f"Publicadas: {enviadas}"))
        if fallidas:
            # Advertencia y no error: lo fallido sigue en la bandeja y se
            # reintentará. Un código de salida distinto de cero haría que un
            # programador de tareas marcara como caída una situación normal
            # durante un corte de red.
            self.stdout.write(
                self.style.WARNING(
                    f"Con fallo (quedan en la bandeja para reintento): {fallidas}"
                )
            )

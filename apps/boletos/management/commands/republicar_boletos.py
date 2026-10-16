"""
Reencola folios ya entregados, para reconstruir el servicio de boletos.

Existe porque la bandeja de salida es una **cola de trabajo y no un
historial**: cuando una publicación se entrega, su fila desaparece. Esa
decisión es correcta para la operación diaria —acota la bandeja al trabajo
pendiente— pero deja un hueco que este comando cierra: nada reenvía lo que ya
se entregó.

Tres situaciones reales lo necesitan:

* **Arranque del servicio.** Al encender la publicación por primera vez, los
  vehículos que ya están dentro no tienen boleto en la nube. Su cliente vería
  «boleto no disponible» justo el primer día.
* **Base de datos nueva o vaciada.** Si el servicio se recrea —cambio de
  proveedor, migración, borrado accidental—, los folios abiertos no se
  republican solos. Se corregirían al cobrarlos, pero mientras el auto está
  dentro, no.
* **Diagnóstico.** Forzar el reenvío de un estado concreto para comprobar qué
  llega al otro extremo.

Es seguro ejecutarlo tantas veces como se quiera: la ingesta del servicio es
idempotente y el encolado es un *upsert*. Lo único que hace una repetición es
trabajo de más.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.boletos.publicador import encolar, publicacion_activa, url_ingesta
from apps.estacionamiento.models import EstadoFolio, Folio


class Command(BaseCommand):
    help = "Reencola folios para republicarlos en el servicio de boletos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--incluir-cerrados",
            action="store_true",
            help=(
                "Reencola también los folios ya liquidados o cancelados cuya "
                "página pública siga vigente."
            ),
        )
        parser.add_argument(
            "--horas",
            type=int,
            default=None,
            help=(
                "Ventana, en horas, para los folios cerrados. Por omisión se "
                "usa la vigencia configurada del boleto público."
            ),
        )
        parser.add_argument(
            "--publicar",
            action="store_true",
            help="Envía de inmediato lo reencolado, sin esperar al hilo de fondo.",
        )
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Informa cuántos folios reencolaría, sin tocar la bandeja.",
        )

    def handle(self, *args, **opciones):
        if not publicacion_activa():
            raise CommandError(
                "La publicación de boletos está desactivada o incompleta. "
                "Revise que estén definidos el origen del servicio y el token, "
                "y que la publicación esté marcada como activa."
            )

        folios = self._seleccionar(opciones)
        total = folios.count()

        self.stdout.write(f"Destino: {url_ingesta()}")
        self.stdout.write(f"Folios a republicar: {total}")

        if opciones["simular"]:
            for folio in folios[:50]:
                self.stdout.write(f"  {folio.uuid} · {folio.placa} · {folio.estado}")
            if total > 50:
                self.stdout.write(f"  … y {total - 50} más.")
            return

        encolados, fallidos = self._encolar(folios)

        self.stdout.write(self.style.SUCCESS(f"Reencolados: {encolados}"))
        if fallidos:
            self.stdout.write(
                self.style.WARNING(
                    f"No se pudieron encolar: {fallidos}. Revise la bitácora."
                )
            )

        if opciones["publicar"]:
            self._publicar()
        elif encolados:
            self.stdout.write(
                "Quedan en la bandeja. El hilo de la caja los enviará, o use "
                "«publicar_boletos» para enviarlos ahora."
            )

    # ------------------------------------------------------------- Selección

    def _seleccionar(self, opciones):
        """
        Folios cuyo boleto público tiene sentido reconstruir.

        Los abiertos son el caso que importa: el vehículo sigue dentro y su
        dueño puede consultar el boleto en cualquier momento.

        Los cerrados se limitan a los que siguen vigentes. Republicar uno más
        antiguo sería trabajo inútil: la vista pública lo contestaría con un
        404 por vencido en cuanto llegara. La ventana por omisión es
        precisamente esa vigencia, de modo que las dos reglas no puedan
        desalinearse.
        """
        # `select_related` no es microoptimización: la instantánea lee la
        # tarifa y el cajón de cada folio, y sin esto el comando haría dos
        # consultas adicionales por boleto contra la base de la caseta.
        consulta = Folio.objects.select_related("tarifa", "cajon")

        if not opciones["incluir_cerrados"]:
            return consulta.filter(estado=EstadoFolio.ABIERTO).order_by("entrada_en")

        horas = opciones["horas"]
        if horas is None:
            horas = int(getattr(settings, "IRCARS_HORAS_VIGENCIA_BOLETO", 24))
        limite = timezone.now() - timedelta(hours=horas)

        self.stdout.write(
            f"Incluyendo folios cerrados o cancelados de las últimas {horas} h."
        )
        # Se filtra por `actualizado_en` y no por `salida_en` porque una
        # cancelación no registra hora de salida: el vehículo no salió por
        # caja. Con `salida_en` los cancelados quedarían fuera siempre.
        return consulta.filter(
            Q(estado=EstadoFolio.ABIERTO)
            | Q(
                estado__in=[EstadoFolio.CERRADO, EstadoFolio.CANCELADO],
                actualizado_en__gte=limite,
            )
        ).order_by("entrada_en")

    # -------------------------------------------------------------- Encolado

    def _encolar(self, folios) -> tuple[int, int]:
        """
        Encola folio por folio, cada uno en su propia transacción.

        No se envuelve el lote completo a propósito: una transacción larga
        sobre SQLite retiene el bloqueo de escritura de toda la base, y este
        comando puede ejecutarse con la caseta operando. Un cobro que se
        quedara esperando a que termine una republicación sería un cliente
        esperando en la rampa.

        La ejecución parcial es inofensiva porque todo el camino es
        idempotente: lo que se encoló se enviará, y lo que no, se vuelve a
        encolar en la siguiente ejecución.
        """
        encolados = fallidos = 0
        for folio in folios.iterator(chunk_size=100):
            if encolar(folio):
                encolados += 1
            else:
                fallidos += 1
        return (encolados, fallidos)

    def _publicar(self) -> None:
        from apps.boletos.publicador import drenar

        self.stdout.write("Enviando…")
        enviadas, con_fallo = drenar(limite=1000)
        self.stdout.write(self.style.SUCCESS(f"Publicados: {enviadas}"))
        if con_fallo:
            self.stdout.write(
                self.style.WARNING(
                    f"Con fallo (quedan en la bandeja para reintento): {con_fallo}"
                )
            )

"""
Enganche entre el folio y la bandeja de publicación.

Se usa una señal y no una llamada dentro de `apps.estacionamiento.services`
por una razón de arquitectura, no de comodidad: el núcleo del negocio no debe
conocer la existencia de la nube. Con la señal, la dependencia apunta en un
solo sentido —boletos → estacionamiento— y la publicación se activa o se
retira instalando o desinstalando esta aplicación, sin tocar una sola línea de
los casos de uso que cobran dinero.

La señal se conecta en `post_save`, por lo que la fila de la bandeja se escribe
**dentro de la transacción del folio**. Es el patrón de bandeja de salida
transaccional: o quedan ambos, o no queda ninguno. Usar `on_commit` sería más
intuitivo y estaría mal: el aviso podría perderse si el proceso muere entre la
confirmación y la ejecución del callback.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def conectar() -> None:
    """Registra los receptores. La invoca `BoletosConfig.ready`."""
    from apps.estacionamiento.models import Folio

    @receiver(
        post_save,
        sender=Folio,
        # `dispatch_uid` hace la conexión idempotente: `ready()` puede
        # ejecutarse más de una vez (recarga del servidor de desarrollo,
        # ciertos arranques de pruebas) y sin él cada folio se encolaría
        # tantas veces como conexiones hubiera.
        dispatch_uid="boletos.encolar_publicacion_de_folio",
        weak=False,
    )
    def _encolar_publicacion(sender, instance, raw=False, **_kwargs):
        # `raw=True` indica una carga de datos con `loaddata`, donde los
        # objetos relacionados pueden no existir todavía. Publicar ahí no
        # tiene sentido y además fallaría al resolver la tarifa.
        if raw:
            return

        from .publicador import encolar, publicacion_activa

        if not publicacion_activa():
            return

        encolar(instance)

"""Registro de la aplicación de publicación de boletos."""
from django.apps import AppConfig


class BoletosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.boletos"
    verbose_name = "Publicación de boletos"

    def ready(self) -> None:
        """
        Conecta la señal que alimenta la bandeja de salida.

        Se hace aquí y no al importar el módulo de modelos porque `ready()` es
        el único punto en que Django garantiza que todas las aplicaciones —en
        particular `apps.estacionamiento`, de la que depende el receptor— ya
        están cargadas.
        """
        from . import senales

        senales.conectar()

"""Registro de la aplicación del boleto público."""
from django.apps import AppConfig


class BoletosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.boletos"
    verbose_name = "Boleto del cliente"

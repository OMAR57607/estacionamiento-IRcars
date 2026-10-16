"""
Rutas públicas del servicio de boletos.

La dirección es corta —`/t/<uuid>/`— y no por estética: el contenido del
código QR determina su densidad. Una ruta como
`/estacionamiento/boletos/consulta/<uuid>/` produciría un símbolo con más
módulos, más difícil de leer para una cámara de gama baja y más grande al
imprimirse en papel térmico de 58 mm.
"""
from django.urls import path

from . import views

app_name = "boletos"

urlpatterns = [
    path("t/<uuid:uuid_folio>/", views.boleto_publico, name="publico"),
]

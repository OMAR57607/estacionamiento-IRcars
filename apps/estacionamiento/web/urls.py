"""Rutas de la interfaz web de caja."""
from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.caja, name="caja"),
    path("turno/", views.turno, name="turno"),
    path("turno/<int:turno_id>/corte/", views.corte, name="corte"),
    path("reportes/", views.reportes, name="reportes"),
    path("tarifa/", views.tarifa, name="tarifa"),
    path("folio/<uuid:uuid>/cancelar/", views.cancelar, name="cancelar"),
]

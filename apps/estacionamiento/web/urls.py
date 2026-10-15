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
    # El cambio de precio vive en su propia dirección y no en un formulario de
    # la pantalla anterior: llegar aquí es un acto deliberado, y la vista exige
    # confirmar la contraseña antes de mostrar nada editable.
    path("tarifa/cambiar/", views.tarifa_cambiar, name="tarifa_cambiar"),
    path("folio/<uuid:uuid>/cancelar/", views.cancelar, name="cancelar"),
]

"""Rutas de la interfaz web de caja."""
from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    # La raíz decide según quién llame: el operador va a la caja, el cliente
    # ve una presentación. Ver `views.inicio`.
    path("", views.inicio, name="inicio"),
    path("caja/", views.caja, name="caja"),
    path("turno/", views.turno, name="turno"),
    path("turno/<int:turno_id>/corte/", views.corte, name="corte"),
    path("reportes/", views.reportes, name="reportes"),
    # Cambio de la propia contraseña. Sin panel de administración, es la única
    # vía de rotar la clave con que se creó una cuenta.
    path("clave/", views.CambiarClave.as_view(), name="clave"),
    path("tarifa/", views.tarifa, name="tarifa"),
    # El cambio de precio vive en su propia dirección y no en un formulario de
    # la pantalla anterior: llegar aquí es un acto deliberado, y la vista exige
    # confirmar la contraseña antes de mostrar nada editable.
    path("tarifa/cambiar/", views.tarifa_cambiar, name="tarifa_cambiar"),
    # Boleto recién emitido: el QR que el cliente escanea y el enlace que se
    # le puede enviar. Va antes de la cancelación por simple orden de uso.
    path("folio/<uuid:uuid>/boleto/", views.boleto, name="boleto"),
    path("folio/<uuid:uuid>/cancelar/", views.cancelar, name="cancelar"),
]

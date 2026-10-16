"""Rutas de la interfaz web de caja."""
from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    # La caja vive en la raíz: es la pantalla que el operador abre todo el día
    # y no debe exigirle recordar una ruta. Quien llegue sin sesión acaba en
    # el formulario de acceso, que es el comportamiento esperado de un
    # sistema de caja publicado en su propia dirección.
    path("", views.caja, name="caja"),
    # Manifiesto de la aplicación instalable. Se genera en lugar de servirse
    # como archivo: así el nombre sale de la configuración y los iconos
    # llevan la huella de su contenido.
    path("manifiesto.json", views.manifiesto, name="manifiesto"),
    path("turno/", views.turno, name="turno"),
    path("turno/<int:turno_id>/corte/", views.corte, name="corte"),
    path("reportes/", views.reportes, name="reportes"),
    # Alta y gobierno de operadores. Sustituye al panel de administración de
    # Django, que no se monta.
    path("operadores/", views.operadores, name="operadores"),
    path("operadores/<int:pk>/", views.operador, name="operador"),
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

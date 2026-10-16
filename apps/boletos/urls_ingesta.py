"""
Ruta de ingesta, en un módulo aparte de las rutas públicas.

La separación es intencional y no cosmética: permite montar la escritura y la
lectura de forma independiente. Un despliegue que solo deba servir boletos
—por ejemplo, una réplica de solo lectura— incluye `urls.py` y no este
archivo, y queda estructuralmente incapaz de aceptar una escritura.
"""
from django.urls import path

from . import views

app_name = "boletos_ingesta"

urlpatterns = [
    path("boletos/", views.IngestaBoletosView.as_view(), name="ingesta"),
]

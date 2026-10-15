"""Enrutamiento raíz del proyecto."""
from django.contrib import admin
from django.http import JsonResponse
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path


def salud(_request):
    """
    Sonda de disponibilidad para supervisión (SRE).

    Deliberadamente no consulta la base de datos: responde si el proceso de
    aplicación está vivo. La verificación de dependencias se expone aparte en
    `/api/v1/salud/` dentro de la aplicación.
    """
    return JsonResponse({"estado": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", salud, name="salud-proceso"),
    # Acceso de los operadores a la interfaz de caja.
    path(
        "acceso/",
        LoginView.as_view(template_name="estacionamiento/acceso.html"),
        name="login",
    ),
    path("salir/", LogoutView.as_view(next_page="login"), name="logout"),
    path("api/v1/", include("apps.estacionamiento.urls")),
    # Login de la interfaz navegable de DRF, útil para probar la API en local.
    path("api-auth/", include("rest_framework.urls")),
    # La interfaz de caja queda en la raíz: es la pantalla que el operador
    # abre todo el día y no debe exigirle recordar una ruta.
    path("", include("apps.estacionamiento.web.urls")),
]

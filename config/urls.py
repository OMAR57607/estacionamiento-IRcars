"""
Enrutamiento raíz del proyecto.

Un mismo código sirve a dos despliegues con propósitos distintos, y el modo lo
decide `IRCARS_SOLO_BOLETOS`:

* **Completo** (omisión): panel de administración, interfaz de caja, API del
  estacionamiento y, además, el servicio de boletos. Es lo que se usa en
  desarrollo y lo que permite probarlo todo en una sola máquina.
* **Solo boletos**: únicamente la consulta pública y la ingesta. Es el modo
  recomendado en la nube, donde la operación real vive en el ejecutable local.
  Publicar ahí la caja o el panel de administración no aportaría nada y sí
  añadiría un formulario de contraseña expuesto a internet.

La diferencia no es cosmética: en modo «solo boletos» las rutas **no existen**.
No hay un permiso que revisar ni una comprobación que pueda fallar por una
configuración mal puesta; sencillamente no están registradas.
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.http import JsonResponse
from django.urls import include, path


def salud(_request):
    """
    Sonda de disponibilidad para supervisión (SRE).

    Deliberadamente no consulta la base de datos: responde si el proceso de
    aplicación está vivo. La verificación de dependencias se expone aparte en
    `/api/v1/salud/` dentro de la aplicación.
    """
    return JsonResponse({"estado": "ok"})


# --- Rutas presentes en todos los modos -------------------------------------
urlpatterns = [
    path("healthz", salud, name="salud-proceso"),
    # Ingesta de boletos. Se declara antes que la API del estacionamiento
    # porque ambas cuelgan de `api/v1/` y el enrutador de DRF registra una
    # vista raíz en ese mismo prefijo.
    path("api/v1/", include("apps.boletos.urls_ingesta")),
    # Consulta pública del boleto: `/t/<uuid>/`.
    path("", include("apps.boletos.urls")),
]

if not settings.IRCARS_SOLO_BOLETOS:
    urlpatterns += [
        path("admin/", admin.site.urls),
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
        # abre todo el día y no debe exigirle recordar una ruta. Va al final
        # porque su patrón vacío captura la raíz del sitio.
        path("", include("apps.estacionamiento.web.urls")),
    ]

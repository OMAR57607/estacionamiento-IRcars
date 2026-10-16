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

from apps.boletos.limites import limitar_por_ip
from apps.estacionamiento.web.forms import FormularioAcceso


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
        # Acceso de los operadores a la interfaz de caja.
        #
        # El formulario está limitado por dirección IP y solo en los envíos:
        # cargar la pantalla no cuenta, de modo que un operador puede
        # recargarla cuanto quiera, pero una lista de contraseñas probada
        # desde una misma dirección se corta en seco. Django no trae esta
        # protección, y con la caja publicada en internet el formulario de
        # acceso es el objetivo más obvio del sistema: detrás hay dinero.
        path(
            "acceso/",
            limitar_por_ip(
                peticiones=10,
                ventana_segundos=300,
                etiqueta="acceso",
                solo_escrituras=True,
            )(
                LoginView.as_view(
                    template_name="estacionamiento/acceso.html",
                    authentication_form=FormularioAcceso,
                )
            ),
            name="login",
        ),
        path("salir/", LogoutView.as_view(next_page="login"), name="logout"),
        path("api/v1/", include("apps.estacionamiento.urls")),
        # Login de la interfaz navegable de DRF, útil para probar la API en local.
        path("api-auth/", include("rest_framework.urls")),
        # La interfaz de caja. Su raíz decide según quién llame —operador a la
        # caja, cliente a una presentación—, por lo que va al final: su patrón
        # vacío captura la raíz del sitio.
        path("", include("apps.estacionamiento.web.urls")),
    ]

# --- Panel de administración de Django --------------------------------------
#
# No se monta salvo que se indique una ruta, y nunca en `/admin/`. Dos razones:
#
# 1. La aplicación no lo necesita para operar. El precio, los turnos, el corte
#    y los reportes tienen sus propias pantallas, pensadas para un operador y
#    con las salvaguardas del negocio —el precio exige confirmar la contraseña,
#    el corte es ciego—. El panel se salta todas ellas.
# 2. `/admin/` es la dirección más sondeada de cualquier sitio Django. Mantener
#    el panel fuera, o en una ruta que nadie adivina, elimina de golpe ese
#    tráfico y la posibilidad de que una contraseña débil de administrador
#    abra la puerta a los datos de caja.
#
# Para habilitarlo puntualmente: IRCARS_RUTA_ADMIN=<cadena-larga-y-aleatoria>
if settings.IRCARS_RUTA_ADMIN:
    urlpatterns += [path(f"{settings.IRCARS_RUTA_ADMIN}/", admin.site.urls)]

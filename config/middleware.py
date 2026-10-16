"""
Middleware propio del proyecto.

Hoy solo contiene la redirección al dominio canónico. Vive aquí, y no en una
aplicación, porque es una decisión de despliegue —a qué dirección responde el
servicio— y no de negocio.
"""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseRedirect

# Rutas que nunca se redirigen.
#
# La sonda de salud la consulta la plataforma contra la dirección interna del
# servicio, no contra el dominio público. Redirigirla haría que una
# comprobación de «¿está vivo el proceso?» respondiera 302 y pudiera
# interpretarse como caída.
RUTAS_EXENTAS = ("/healthz",)

# Métodos que se pueden redirigir sin perder nada.
#
# Un POST redirigido es un problema serio y silencioso: el navegador descarta
# el cuerpo y repite la petición como GET. En este sistema eso significaría un
# cobro que el operador da por hecho y que nunca llegó al servidor. Las
# escrituras se dejan pasar: el dominio anterior sigue sirviendo, con su propia
# sesión y su propio testigo CSRF, así que funcionan con normalidad.
METODOS_REDIRIGIBLES = ("GET", "HEAD")


class RedirigirAlDominioCanonico:
    """
    Lleva al dominio propio a quien llegue por una dirección anterior.

    El motivo no es cosmético. **Los códigos QR ya emitidos llevan grabado el
    dominio con el que se generaron**, y un boleto impreso o compartido por
    WhatsApp antes del cambio seguirá apuntando ahí durante días. Sin esta
    redirección, esos clientes verían la página en la dirección antigua —o
    nada, si algún día se retira—.

    Esto no puede resolverse en Cloudflare: Cloudflare solo ve el tráfico de
    la zona que administra, y la dirección de la plataforma no pertenece a
    ella. Tiene que decidirlo la aplicación, que es quien recibe ambas.

    Se usa 302 y no 301 a propósito. Un 301 queda cacheado en el navegador de
    cada operador y **no hay forma de retirarlo en remoto**: si el dominio
    canónico se configurara mal, todos los que lo hubieran visitado quedarían
    atrapados apuntando a una dirección equivocada. Para un sistema interno,
    que además no se indexa, el 301 no aporta nada que compense ese riesgo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        destino = self._destino(request)
        if destino is not None:
            return HttpResponseRedirect(destino)
        return self.get_response(request)

    @staticmethod
    def _destino(request) -> str | None:
        """
        Dirección a la que redirigir, o `None` si la petición se queda.

        La configuración se lee en cada petición y no al construir el
        middleware. Es un `getattr` y una comparación de cadenas —coste
        irrelevante— y a cambio el comportamiento se puede probar cambiando el
        ajuste, que es lo que no permitiría decidirlo una sola vez al arrancar.
        """
        canonico = str(getattr(settings, "IRCARS_DOMINIO_CANONICO", "")).strip().lower()
        if not canonico:
            return None

        if request.method not in METODOS_REDIRIGIBLES:
            return None
        if request.path in RUTAS_EXENTAS:
            return None

        # Se compara sin el puerto: el host de una petición en desarrollo
        # incluye `:8000` y no debe tomarse por un dominio distinto.
        actual = request.get_host().split(":")[0].lower()
        if actual == canonico:
            return None

        # Siempre a https: el dominio canónico se sirve cifrado y encadenar
        # una segunda redirección hacia HTTPS sería un salto de más en el
        # teléfono de un cliente que está escaneando un boleto.
        return f"https://{canonico}{request.get_full_path()}"

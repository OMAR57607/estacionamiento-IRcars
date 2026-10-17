"""
Accesorios compartidos por todas las pruebas.

Vive aquí, y no en el paquete de una aplicación, porque lo que resuelve afecta
a todas: los límites de tasa llevan su cuenta en el caché de Django, que es un
proceso compartido durante la sesión de pruebas completa.
"""
import pytest


@pytest.fixture(autouse=True)
def limpiar_cache():
    """
    Vacía el caché entre pruebas.

    Sin esto, una prueba que agota un límite —la que comprueba que una lista
    de contraseñas se corta, por ejemplo— dejaría el contador alto y haría
    fallar a la siguiente por un motivo ajeno a lo que examina. El síntoma es
    especialmente engañoso: la prueba falla o no según el orden de ejecución.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def estaticos_sin_manifiesto(settings):
    """
    Estáticos servidos por ruta directa durante las pruebas.

    En producción se versionan por contenido y la plantilla resuelve
    `{% static %}` contra el manifiesto que escribe `collectstatic`. Ese
    manifiesto no existe en el árbol de trabajo, de modo que **cualquier
    plantilla que extienda la base falla al dibujarse** con
    `Missing staticfiles manifest entry`, por un detalle de despliegue y no
    de comportamiento.

    Vive aquí y no en cada suite porque el problema aparece en cuanto una
    plantilla referencia un estático nuevo: estaba copiado en cinco archivos
    y cada icono añadido obligaba a recordar cuál faltaba.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture(autouse=True)
def sin_redireccion_a_https(settings):
    """
    Desactiva `SECURE_SSL_REDIRECT` durante las pruebas.

    En producción está activo —con `DEBUG=False`— y es lo correcto: una caja
    publicada en internet no debe atender nada por HTTP. Pero el cliente de
    pruebas habla HTTP salvo que se le pida `secure=True`, de modo que con la
    redirección encendida medio centenar de pruebas recibía un 301 en lugar
    de la página, y la suite solo pasaba con `DEBUG=True`.

    Esa dependencia oculta es justo lo que no debe tener un conjunto de
    pruebas: el resultado cambiaba según una variable de entorno ajena a lo
    que se examina, y quien lo descubría era quien montaba la integración
    continua. Fijándola aquí, la suite da el mismo resultado con cualquier
    valor de `DEBUG`, y las pruebas que sí examinan la superficie expuesta
    piden `secure=True` explícitamente, como hace la mayoría.
    """
    settings.SECURE_SSL_REDIRECT = False

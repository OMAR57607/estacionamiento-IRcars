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

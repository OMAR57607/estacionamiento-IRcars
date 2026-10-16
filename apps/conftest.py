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

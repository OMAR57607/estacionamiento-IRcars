"""
Configuración de rutas vacía, para la aplicación de escritorio.

La aplicación de escritorio no expone ninguna URL: no hay servidor, no hay
puertos y la interfaz invoca los servicios directamente en el mismo proceso.
Aun así Django requiere que `ROOT_URLCONF` apunte a algún módulo, y el gancho
de PyInstaller para Django lo lee sin comprobar que exista —da por hecho que
todo proyecto Django es web—.

Declararlo aquí, vacío y explícito, es preferible a apuntar a `config.urls`:
aquel importa Django REST Framework y el panel de administración, que no
intervienen en el ejecutable y solo engordarían el empaquetado.
"""

urlpatterns: list = []

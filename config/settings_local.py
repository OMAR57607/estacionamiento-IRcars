"""
Configuración del **ejecutable local** (aplicación de escritorio).

Difiere de `config/settings.py` en tres aspectos deliberados:

1. **Sin servidor ni red.** No hay `ALLOWED_HOSTS`, ni middleware, ni URLs. Django
   se usa como biblioteca de acceso a datos: la interfaz invoca los servicios
   directamente, en el mismo proceso. No se abre ningún puerto, lo que elimina
   de raíz toda la superficie de ataque de una aplicación web.

2. **SQLite en la carpeta de datos del usuario.** Un único archivo, respaldable
   copiándolo. El ejecutable no escribe en su propio directorio —en Windows
   suele estar en `Program Files`, sin permiso de escritura—, sino en
   `%LOCALAPPDATA%\\IRcars`.

3. **Aplicaciones mínimas.** Sin admin, sin sesiones, sin archivos estáticos y
   sin Django REST Framework. Menos código cargado es menos peso en el
   ejecutable, menos tiempo de arranque y menos superficie de fallo.

Nota operativa importante: SQLite **no implementa bloqueo de fila**, por lo que
`select_for_update` es inocuo en este motor. La protección contra doble
ocupación y doble cobro recae en los índices únicos parciales —que SQLite sí
respeta— y en el bloqueo de escritura de toda la base que SQLite aplica por
naturaleza. Para una caja en un solo equipo esto es suficiente y correcto. Si
en el futuro operan varias casetas en red, el motor debe ser PostgreSQL, y para
eso existe `config/settings.py`.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

NOMBRE_CARPETA = "IRcars"


def _directorio_datos() -> Path:
    """
    Carpeta de datos del usuario, según la convención de cada sistema.

    Se respeta la ubicación estándar de cada plataforma para que los respaldos
    del sistema operativo la incluyan y para no requerir privilegios de
    administrador.
    """
    # Permite reubicar los datos sin recompilar: útil para apuntar a una
    # carpeta respaldada automáticamente o a una unidad distinta.
    personalizado = os.environ.get("IRCARS_DIR_DATOS")
    if personalizado:
        ruta = Path(personalizado)
    elif sys.platform == "win32":
        ruta = Path(os.environ.get("LOCALAPPDATA", Path.home())) / NOMBRE_CARPETA
    elif sys.platform == "darwin":
        ruta = Path.home() / "Library" / "Application Support" / NOMBRE_CARPETA
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        ruta = Path(base) / NOMBRE_CARPETA

    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


DIRECTORIO_DATOS = _directorio_datos()
RUTA_BASE_DATOS = DIRECTORIO_DATOS / "estacionamiento.sqlite3"
RUTA_BITACORA = DIRECTORIO_DATOS / "bitacora.log"


def _clave_secreta() -> str:
    """
    Clave criptográfica propia de esta instalación, generada en el primer uso.

    No se incrusta en el ejecutable a propósito: una clave compilada sería
    idéntica en todas las copias distribuidas y, al ser pública de hecho,
    inservible como secreto. Aquí cada instalación tiene la suya, almacenada
    con permisos restringidos.
    """
    archivo = DIRECTORIO_DATOS / "clave.secreta"
    if archivo.exists():
        clave = archivo.read_text(encoding="utf-8").strip()
        if clave:
            return clave

    clave = secrets.token_urlsafe(50)
    archivo.write_text(clave, encoding="utf-8")
    try:
        # Solo el propietario puede leerla. En Windows el modelo de permisos
        # es distinto y esta llamada es inocua, de ahí la tolerancia al fallo.
        os.chmod(archivo, 0o600)
    except OSError:
        pass
    return clave


SECRET_KEY = _clave_secreta()

# La aplicación de escritorio nunca debe correr en modo depuración: expondría
# trazas completas en los diálogos de error.
DEBUG = False
ALLOWED_HOSTS: list[str] = []

# Mínimo indispensable: tipos de contenido (requerido por el ORM) y
# autenticación (los operadores, de quienes depende la trazabilidad del corte).
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "apps.estacionamiento",
    # Bandeja de salida de boletos. Añade una tabla pequeña y una señal; no
    # abre ningún puerto ni contacta a nadie mientras la publicación esté
    # desactivada, que es como viene de fábrica.
    "apps.boletos",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(RUTA_BASE_DATOS),
        "OPTIONS": {
            # Espera hasta 20 s si la base está bloqueada por otra escritura,
            # en lugar de fallar de inmediato ante un pico de concurrencia.
            "timeout": 20,
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# La aplicación de escritorio no expone URLs, pero Django exige que
# `ROOT_URLCONF` apunte a algún módulo y no le da valor por omisión —a
# diferencia de `MIDDLEWARE` o `TEMPLATES`—. El gancho de PyInstaller para
# Django también lo lee sin comprobar que exista, dando por hecho que todo
# proyecto Django es web: sin esta línea, la compilación del ejecutable falla
# con `AttributeError: 'Settings' object has no attribute 'ROOT_URLCONF'`.
ROOT_URLCONF = "config.urls_vacio"

LANGUAGE_CODE = "es-mx"
TIME_ZONE = os.environ.get("IRCARS_ZONA_HORARIA", "America/Mexico_City")
USE_I18N = True
# Marcas de tiempo almacenadas en UTC: el cálculo de permanencia permanece
# correcto en los cambios de horario de verano.
USE_TZ = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Bitácora en archivo: en un ejecutable sin consola, es el único rastro
# disponible para reconstruir qué ocurrió ante una aclaración o una falla.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detallado": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(RUTA_BITACORA),
            # 2 MB por archivo y 5 respaldos: suficiente para meses de
            # operación sin llenar el disco de la caja.
            "maxBytes": 2 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "detallado",
        },
    },
    "root": {"handlers": ["archivo"], "level": "INFO"},
    "loggers": {
        "apps.estacionamiento": {
            "handlers": ["archivo"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


# --- Publicación de boletos (consulta del cliente por QR) -------------------
#
# Desactivada mientras no se configure. Una caja que se actualice sin haber
# contratado el servicio no debe empezar a emitir peticiones a internet por el
# solo hecho de instalar la versión nueva.
#
# La configuración se lee de un archivo JSON en la carpeta de datos y no de
# variables de entorno. El motivo es práctico: en la caseta, el programa se
# abre con un doble clic desde un acceso directo, y nadie va a definir
# variables de entorno en ese equipo. Un archivo que se edita con el Bloc de
# notas sí es operable por quien administra el estacionamiento.
#
#   %LOCALAPPDATA%\IRcars\boletos.json
#   {
#     "activo": true,
#     "url": "https://boletos.mi-estacionamiento.mx",
#     "token": "<el mismo valor que IRCARS_TOKEN_PUBLICACION en el servicio>"
#   }

ARCHIVO_PUBLICACION = DIRECTORIO_DATOS / "boletos.json"


def _configuracion_publicacion() -> dict:
    """
    Lee la configuración del servicio de boletos. Nunca falla.

    Un archivo ausente, vacío o con JSON inválido deja la publicación
    desactivada y lo registra, en lugar de impedir que la aplicación abra: la
    caja debe poder cobrar aunque la configuración del boleto esté rota.
    """
    import json

    if not ARCHIVO_PUBLICACION.exists():
        return {}
    try:
        contenido = json.loads(ARCHIVO_PUBLICACION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # No se usa `logging` aquí: este módulo se evalúa antes de que la
        # configuración de bitácora exista.
        return {}
    return contenido if isinstance(contenido, dict) else {}


_publicacion = _configuracion_publicacion()

# El entorno tiene prioridad sobre el archivo: así el flujo de pruebas y el
# desarrollo pueden apuntar a otro servicio sin tocar los datos del usuario.
IRCARS_URL_BOLETOS = os.environ.get("IRCARS_URL_BOLETOS") or _publicacion.get("url", "")
IRCARS_TOKEN_PUBLICACION = os.environ.get("IRCARS_TOKEN_PUBLICACION") or _publicacion.get(
    "token", ""
)
IRCARS_PUBLICAR_BOLETOS = bool(_publicacion.get("activo", False))
if os.environ.get("IRCARS_PUBLICAR_BOLETOS"):
    IRCARS_PUBLICAR_BOLETOS = os.environ["IRCARS_PUBLICAR_BOLETOS"].lower() in (
        "1",
        "true",
        "si",
        "sí",
    )

# Solo para pruebas en la máquina local: permite publicar contra un servicio
# en http://localhost. En cualquier otro caso el publicador exige HTTPS,
# porque el token viaja en la petición.
IRCARS_PERMITIR_HTTP_BOLETOS = os.environ.get(
    "IRCARS_PERMITIR_HTTP_BOLETOS", ""
).lower() in ("1", "true", "si", "sí")

# El archivo contiene una credencial: se restringe su lectura al propietario.
# En Windows el modelo de permisos es distinto y la llamada es inocua.
if ARCHIVO_PUBLICACION.exists():
    try:
        os.chmod(ARCHIVO_PUBLICACION, 0o600)
    except OSError:
        pass

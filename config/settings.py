"""
Configuración de Django para el controlador de estacionamiento IRcars.

Toda la configuración sensible o dependiente del entorno se lee de variables
de entorno (patrón 12-factor): el mismo código corre sin cambios en la laptop
del desarrollador, en el equipo instalado en el estacionamiento y en un
servidor, variando únicamente el archivo `.env`.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_TIME_ZONE=(str, "America/Mexico_City"),
    DJANGO_LOG_LEVEL=(str, "INFO"),
)

# Lectura del `.env` si existe. En producción las variables suelen inyectarse
# directamente en el entorno del proceso, por lo que el archivo es opcional.
_archivo_env = BASE_DIR / ".env"
if _archivo_env.exists():
    env.read_env(_archivo_env)

# --- Seguridad --------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# Endurecimiento activo solo fuera de desarrollo: en local HTTPS no aplica y
# forzarlo impediría probar la API en http://localhost.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000  # 1 año
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

# --- Aplicaciones -----------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.estacionamiento",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Persistencia -----------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}

# Conexiones persistentes: evita el costo de reconectar en cada petición,
# relevante en una caja que registra entradas y salidas de forma continua.
DATABASES["default"]["CONN_MAX_AGE"] = 60

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Validación de contraseñas ----------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internacionalización ---------------------------------------------------
LANGUAGE_CODE = "es-mx"
TIME_ZONE = env("DJANGO_TIME_ZONE")
USE_I18N = True
# Las marcas de tiempo se almacenan en UTC y se presentan en TIME_ZONE.
# Indispensable para que el cálculo de permanencia sea correcto en los
# cambios de horario.
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- API --------------------------------------------------------------------
REST_FRAMEWORK = {
    # Traduce las excepciones del dominio a códigos HTTP en un solo lugar,
    # de modo que los controladores no repitan bloques try/except.
    "EXCEPTION_HANDLER": "apps.estacionamiento.api_errors.manejador_excepciones",
    # El acceso a la API es siempre autenticado: registrar una entrada o
    # cobrar una salida son operaciones con efecto económico.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # Límite de tasa: contiene un operador o un script que dispare peticiones
    # en exceso por error o por abuso.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "600/min"},
}

# --- Observabilidad ---------------------------------------------------------
# Los eventos de negocio (entrada, cobro, cierre) se registran con su folio
# para permitir trazar cualquier movimiento ante una aclaración del cliente.
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
        "consola": {
            "class": "logging.StreamHandler",
            "formatter": "detallado",
        },
    },
    "root": {
        "handlers": ["consola"],
        "level": env("DJANGO_LOG_LEVEL"),
    },
    "loggers": {
        "apps.estacionamiento": {
            "handlers": ["consola"],
            "level": env("DJANGO_LOG_LEVEL"),
            "propagate": False,
        },
    },
}

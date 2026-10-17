"""
Configuración de Django para el controlador de estacionamiento IRcars.

Toda la configuración sensible o dependiente del entorno se lee de variables
de entorno (patrón 12-factor): el mismo código corre sin cambios en la laptop
del desarrollador, en el equipo instalado en el estacionamiento y en un
servidor, variando únicamente el archivo `.env`.
"""
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

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

# Railway asigna el dominio público en tiempo de despliegue y puede cambiarlo.
# Leerlo del entorno evita tener que recordar actualizar una lista fija cada
# vez que se regenera el dominio —olvido que se manifiesta como un 400 Bad
# Request sin explicación visible—.
_dominio_publico = env("RAILWAY_PUBLIC_DOMAIN", default="")
_dominio_privado = env("RAILWAY_PRIVATE_DOMAIN", default="")
for _dominio in (_dominio_publico, _dominio_privado):
    if _dominio and _dominio not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_dominio)

# El panel de administración envía formularios POST: sin el origen declarado,
# Django los rechaza por CSRF y el acceso resulta imposible.
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
if _dominio_publico:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_dominio_publico}")

# Railway termina el TLS en su proxy y reenvía la petición por HTTP interno.
# Sin esta cabecera, Django consideraría insegura toda petición y, con
# SECURE_SSL_REDIRECT activo, entraría en un bucle infinito de redirecciones.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

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
    # Publicación de boletos para consulta del cliente por código QR. Es una
    # aplicación desacoplada: el estacionamiento no la importa en ningún punto,
    # de modo que puede retirarse de esta lista sin tocar el núcleo.
    "apps.boletos",
]

MIDDLEWARE = [
    # Va en primer lugar para resolver el dominio de una sola vez. Si fuera
    # después de SecurityMiddleware, una petición al dominio anterior por HTTP
    # daría dos saltos —primero a HTTPS en el dominio viejo, después al
    # canónico— y cada salto cuenta en el teléfono de un cliente que está
    # escaneando su boleto.
    "config.middleware.RedirigirAlDominioCanonico",
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise sirve los archivos estáticos del panel de administración
    # desde el propio proceso. En Railway no hay nginx delante, y sin esto el
    # admin se vería sin hojas de estilo ni JavaScript.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# --- Acceso a la interfaz de caja -------------------------------------------
LOGIN_URL = "login"
# Tras autenticarse, el operador va directo a la pantalla de caja: es lo que
# necesita, y cualquier paso intermedio es tiempo perdido frente al cliente.
LOGIN_REDIRECT_URL = "web:caja"
LOGOUT_REDIRECT_URL = "login"

# La sesión expira al cerrar el navegador y tras dos horas de inactividad.
# En una caseta compartida, una sesión abierta indefinidamente permitiría a
# otra persona cobrar con la identidad del operador anterior, lo que rompería
# la atribución del corte.
SESSION_COOKIE_AGE = 2 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

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
                # El nombre comercial se resuelve en un solo lugar: sin esto,
                # cada plantilla lo escribía a mano y se desincronizaban.
                "apps.estacionamiento.marca.contexto",
            ],
        },
    },
]

# --- Persistencia -----------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}

# Aislamiento por esquema de PostgreSQL.
#
# Permite alojar este sistema en una instancia compartida con otras
# aplicaciones sin riesgo de colisión: todas las tablas —incluida
# `django_migrations`— se crean dentro del esquema indicado.
#
# ADVERTENCIA IMPORTANTE: el `search_path` contiene EXCLUSIVAMENTE nuestro
# esquema. Nunca debe añadirse `public`. Si `public` estuviera en la ruta de
# búsqueda y otra aplicación Django tuviera ahí su propia `django_migrations`,
# nuestro `migrate` leería el historial ajeno, concluiría que las migraciones
# de `auth` y `contenttypes` ya están aplicadas y omitiría crear esas tablas en
# nuestro esquema. El resultado sería un esquema incompleto que falla en la
# primera consulta, con un error que no apunta a su causa.
#
# `pg_catalog` siempre se resuelve de forma implícita, de modo que las
# funciones y tipos del sistema siguen disponibles sin incluir `public`.
_esquema = env("DJANGO_DB_SCHEMA", default="")
if _esquema:
    if "postgresql" not in DATABASES["default"]["ENGINE"]:
        raise ImproperlyConfigured(
            "DJANGO_DB_SCHEMA solo aplica a PostgreSQL: SQLite no tiene esquemas."
        )
    DATABASES["default"].setdefault("OPTIONS", {})["options"] = (
        f"-c search_path={_esquema}"
    )

# Conexiones persistentes: evita el costo de reconectar en cada petición,
# relevante en una caja que registra entradas y salidas de forma continua.
# No se aplica a SQLite: ahí la "conexión" es un descriptor de archivo local,
# sin costo de establecimiento, y mantenerla abierta solo prolongaría los
# bloqueos de escritura sobre el archivo.
if "sqlite" not in DATABASES["default"]["ENGINE"]:
    DATABASES["default"]["CONN_MAX_AGE"] = 60
else:
    # Espera a que se libere el bloqueo de escritura en lugar de fallar de
    # inmediato: SQLite serializa a los escritores y un pico de peticiones
    # concurrentes produciría "database is locked" sin este margen.
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 20

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

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Comprime y versiona los estáticos por contenido: el navegador puede
        # almacenarlos en caché indefinidamente y aun así recibir la versión
        # nueva tras un despliegue.
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

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
    #
    # `UserRateThrottle` por sí solo **no cubre al tráfico anónimo**: solo
    # cuenta peticiones de usuarios autenticados y deja sin tope cualquier
    # ruta sin sesión. Mientras toda la API exigía autenticación eso no se
    # notaba; con el servicio de boletos existen rutas que se consultan sin
    # credenciales, y la omisión pasaría a ser una vía de agotamiento del
    # servicio. `AnonRateThrottle` cierra ese hueco.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "600/min",
        "anon": "60/min",
    },
}

# --- Boleto del cliente (consulta por QR) -----------------------------------
#
# El boleto se construye leyendo el folio, que es la fuente de verdad y vive
# en esta misma base. La página es pública y de solo lectura: lo único que
# sale de aquí es una placa enmascarada y el precio exhibido al ingresar.

# Nombre que se muestra en el boleto del cliente.
IRCARS_NOMBRE_COMERCIAL = env("IRCARS_NOMBRE_COMERCIAL", default="Estacionamiento")

# Origen público del servicio: lo que se codifica en el QR del boleto.
IRCARS_URL_BOLETOS = env("IRCARS_URL_BOLETOS", default="")
if not IRCARS_URL_BOLETOS and _dominio_publico:
    IRCARS_URL_BOLETOS = f"https://{_dominio_publico}"

# Horas que un boleto liquidado sigue consultándose. Mientras el vehículo está
# dentro el enlace no caduca; una vez cobrado, deja de responder y reduce la
# exposición de una dirección que vive en historiales y mensajes reenviados.
IRCARS_HORAS_VIGENCIA_BOLETO = env.int("IRCARS_HORAS_VIGENCIA_BOLETO", default=24)

# El servicio corre detrás del proxy de la plataforma, que termina el TLS. De
# ello depende cómo se determina la IP del solicitante al aplicar límites de
# tasa (ver `apps.boletos.limites.ip_cliente`). Póngase en False si el proceso
# atiende peticiones directamente, donde `X-Forwarded-For` no sería de fiar.
IRCARS_TRAS_PROXY = env.bool("IRCARS_TRAS_PROXY", default=True)

# El servicio está publicado detrás de Cloudflare. Cambia de qué cabecera se
# toma la IP del cliente al aplicar los límites de tasa: con Cloudflare en
# medio, la entrada derecha de `X-Forwarded-For` es la IP de su borde y no la
# del cliente, de modo que todos los visitantes compartirían un puñado de
# contadores. Ver `apps.boletos.limites.ip_cliente`.
#
# Desactivada por omisión: activarla sin estar realmente detrás de Cloudflare
# haría confiable una cabecera que cualquiera podría inventar.
IRCARS_TRAS_CLOUDFLARE = env.bool("IRCARS_TRAS_CLOUDFLARE", default=False)

# Dominio propio del servicio. Si se define, toda lectura que llegue por otra
# dirección —la que asigna la plataforma, un dominio anterior— se redirige
# aquí. Importa porque los códigos QR ya emitidos llevan grabado el dominio
# con el que se generaron. Vacío desactiva la redirección por completo.
IRCARS_DOMINIO_CANONICO = env("IRCARS_DOMINIO_CANONICO", default="")

# Ruta del panel de administración de Django. Vacía —lo normal— significa que
# el panel NO se monta en ninguna dirección. La aplicación no lo necesita para
# operar, y `/admin/` es la ruta más sondeada de cualquier sitio Django.
# Para habilitarlo puntualmente, use una cadena larga y aleatoria, sin barras.
IRCARS_RUTA_ADMIN = env("IRCARS_RUTA_ADMIN", default="").strip("/")

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

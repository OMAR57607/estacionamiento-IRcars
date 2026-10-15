# -*- mode: python ; coding: utf-8 -*-
"""
Especificación de PyInstaller para el ejecutable de caja.

El problema central al empaquetar una aplicación Django no es el código, sino
lo que el análisis estático de PyInstaller **no puede ver**. Django descubre
muchas cosas en tiempo de ejecución —migraciones, configuraciones de
aplicación, el módulo de ajustes— recorriendo paquetes por nombre. Nada de eso
aparece como un `import` en el código, así que PyInstaller lo deja fuera y el
ejecutable falla en el primer uso con un error que no apunta a la causa.

Por eso cada importación oculta declarada abajo responde a un fallo concreto y
previsible, y está anotada con cuál.

Construcción:
    pyinstaller --noconfirm --clean ircars.spec

Verificación del binario resultante:
    dist/IRcars-Caja.exe --verificar     (debe terminar con código 0)
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- Datos empaquetados -----------------------------------------------------

datas = []

# Base de datos horaria IANA. Es el fallo más probable en Windows: el módulo
# `zoneinfo` de Python la busca en el sistema operativo y Windows no la
# incluye, de modo que depende del paquete `tzdata`. Sin estos archivos, una
# zona como "America/Mexico_City" lanza ZoneInfoNotFoundError al arrancar.
datas += collect_data_files("tzdata")

# --- Importaciones ocultas --------------------------------------------------

hiddenimports = []

# Migraciones. Django las localiza recorriendo el paquete `migrations` de cada
# aplicación en tiempo de ejecución. Sin declararlas, el ejecutable arranca
# contra una base de datos sin tablas.
hiddenimports += collect_submodules("apps.estacionamiento.migrations")
hiddenimports += collect_submodules("django.contrib.auth.migrations")
hiddenimports += collect_submodules("django.contrib.contenttypes.migrations")

# Módulos que Django carga por nombre a partir de la configuración: el propio
# módulo de ajustes, la clase de configuración de la aplicación y los modelos.
# Se enumeran de forma explícita, y no con `collect_submodules` sobre toda la
# aplicación, para no arrastrar los módulos de la interfaz web (`views.py`,
# `serializers.py`), que importan Django REST Framework y no tienen ningún
# papel en el ejecutable de escritorio.
hiddenimports += [
    "config.settings_local",
    "config.urls_vacio",
    "apps.estacionamiento",
    "apps.estacionamiento.apps",
    "apps.estacionamiento.models",
    "apps.estacionamiento.services",
    "apps.estacionamiento.services_caja",
    "apps.estacionamiento.domain.tarifas",
    "apps.estacionamiento.domain.corte",
    "apps.estacionamiento.domain.exceptions",
    "apps.estacionamiento.management.commands.sembrar_demo",
]

# Backends y utilidades que Django importa por cadena de texto desde la
# configuración, nunca con un `import` literal.
hiddenimports += [
    "django.db.backends.sqlite3",
    "django.db.backends.sqlite3.base",
    "django.contrib.auth.hashers",
    "django.contrib.auth.password_validation",
    "logging.handlers",
]

# --- Exclusiones ------------------------------------------------------------

# Todo lo que solo usa el despliegue web. Excluirlo reduce el tamaño del
# ejecutable y, sobre todo, evita empaquetar los binarios del controlador de
# PostgreSQL, que la aplicación de escritorio no utiliza: su base de datos es
# SQLite, en el equipo del operador.
excludes = [
    "rest_framework",
    "psycopg",
    "psycopg_binary",
    "psycopg2",
    "whitenoise",
    "gunicorn",
    "pytest",
    "_pytest",
    "pytest_django",
    "environ",
    # Bibliotecas científicas que a veces se cuelan desde el entorno y pesan
    # decenas de megabytes sin que nada las necesite aquí.
    "numpy",
    "pandas",
    "matplotlib",
    "PIL",
]


a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# --- Ejecutable de operación -----------------------------------------------

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IRcars-Caja",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX desactivado a propósito: comprime el binario, pero los antivirus de
    # Windows marcan con frecuencia los ejecutables comprimidos con UPX como
    # sospechosos. Un falso positivo en el equipo de la caseta costaría mucho
    # más que los megabytes que ahorra.
    upx=False,
    runtime_tmpdir=None,
    # Sin consola: es una aplicación de ventana. Los errores de arranque se
    # informan en un diálogo y quedan en la bitácora, según main.py.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


# --- Ejecutable de diagnóstico ---------------------------------------------

# Misma aplicación, pero CON consola. Comparte el mismo análisis, así que no
# añade tiempo de compilación apreciable ni puede divergir del ejecutable real.
#
# Existe porque un programa de ventana que falla antes de dibujar su primera
# ventana no deja nada en pantalla: el operador solo ve que «no abre». Con esta
# versión, el error aparece escrito en la consola y puede copiarse tal cual.
#
# No es la que se usa a diario —abre una ventana negra de consola junto a la
# aplicación—, sino la que se ejecuta cuando algo va mal.
exe_diagnostico = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IRcars-Caja-diagnostico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

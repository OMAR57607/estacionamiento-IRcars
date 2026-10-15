"""Configuración de la aplicación."""
import logging

from django.apps import AppConfig
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)


def _configurar_sqlite(sender, connection, **kwargs):
    """
    Ajusta SQLite en cada conexión nueva.

    Se aplica a cualquier despliegue que use SQLite —la aplicación de
    escritorio y el servicio desplegado con base de datos local— sin duplicar
    la configuración en cada módulo de ajustes.

    * **WAL**: registro de escritura anticipada. Ante un apagón o un reinicio
      abrupto del contenedor, la base se recupera de forma consistente en lugar
      de quedar corrupta. En una caja esto equivale a no perder los cobros del
      turno. Además permite leer mientras se escribe, de modo que el tablero de
      vehículos no espera a que termine un cobro.
    * **synchronous=NORMAL**: equilibrio correcto junto con WAL. Conserva la
      integridad ante caída del proceso sin pagar una sincronización de disco
      por transacción.
    * **foreign_keys=ON**: SQLite desactiva la integridad referencial por
      omisión. Sin esto, las llaves foráneas declaradas en los modelos no se
      harían cumplir y un folio podría quedar apuntando a un turno inexistente.
    """
    if connection.vendor != "sqlite":
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        # Un fallo aquí no debe impedir el arranque, pero tampoco pasar
        # inadvertido: sin WAL la base queda expuesta a un corte de energía.
        logger.warning("No fue posible aplicar la configuración de SQLite", exc_info=True)


# Registro de bases ya verificadas, por (alias, nombre de base). Evita
# ejecutar una sentencia extra en cada conexión nueva —costo inútil en un
# servicio que atiende peticiones de forma continua—.
#
# La clave incluye el nombre de la base y no solo el alias: Django crea una
# base distinta para las pruebas (`test_<nombre>`), y una bandera por proceso
# haría que la conexión a la base principal marcara el trabajo como hecho,
# dejando la base de pruebas sin esquema y con un fallo en `CREATE TABLE`.
_esquemas_verificados: set[tuple[str, str]] = set()


def _asegurar_esquema(sender, connection, **kwargs):
    """
    Crea el esquema configurado si aún no existe.

    Con `search_path` apuntando a un esquema inexistente, PostgreSQL rechaza
    cualquier `CREATE TABLE` con «no schema has been selected to create in»,
    un mensaje que no señala la causa real. Esto ocurre en dos momentos
    legítimos: al preparar la base de datos de pruebas, que Django crea desde
    cero, y en el primer despliegue contra una instancia nueva.

    `CREATE SCHEMA` es independiente del `search_path`, de modo que funciona
    aunque la ruta de búsqueda apunte a un esquema todavía ausente.

    Si el usuario de la base no tiene permiso para crear esquemas —lo habitual
    en una instancia administrada bien configurada—, el fallo se registra y no
    se propaga: el esquema debe haberse creado por administración, y abortar
    aquí dejaría el servicio sin arrancar por una comprobación preventiva.
    """
    if connection.vendor != "postgresql":
        return

    clave = (connection.alias, connection.settings_dict.get("NAME") or "")
    if clave in _esquemas_verificados:
        return

    opciones = (connection.settings_dict.get("OPTIONS") or {}).get("options", "")
    if "search_path=" not in opciones:
        _esquemas_verificados.add(clave)
        return

    esquema = opciones.split("search_path=", 1)[1].split()[0].split(",")[0].strip()
    if not esquema:
        _esquemas_verificados.add(clave)
        return

    try:
        with connection.cursor() as cursor:
            # Identificador citado: evita inyección a través del nombre del
            # esquema, que proviene de una variable de entorno.
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{esquema}";')
        logger.debug("Esquema «%s» verificado", esquema)
    except Exception:
        logger.warning(
            "No fue posible verificar el esquema «%s». Si no existe, debe "
            "crearse por administración de la base de datos.",
            esquema,
            exc_info=True,
        )
    finally:
        _esquemas_verificados.add(clave)


class EstacionamientoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.estacionamiento"
    verbose_name = "Estacionamiento IRcars"

    def ready(self):
        # weak=False es indispensable: las señales de Django guardan
        # referencias débiles y, sin esto, el manejador sería recolectado
        # antes de su primer uso. El fallo sería silencioso —la base quedaría
        # en modo `delete` y solo se descubriría con un apagón—.
        connection_created.connect(_configurar_sqlite, weak=False)
        connection_created.connect(_asegurar_esquema, weak=False)

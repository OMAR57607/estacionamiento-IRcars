"""
Excepciones del dominio.

Son independientes de HTTP y de Django a propósito: el dominio expresa qué
regla de negocio se violó, y la capa de interfaz decide cómo traducirlo a un
código de respuesta. Así el mismo núcleo sirve a la API, a un panel de caja o
a un controlador de hardware.
"""


class ErrorDominio(Exception):
    """Raíz de la jerarquía. Permite capturar cualquier error de negocio."""


class ErrorConfiguracion(ErrorDominio):
    """Parámetros de tarifa inconsistentes o ausentes."""


class SinCajonesDisponibles(ErrorDominio):
    """No existe cajón libre del tipo solicitado."""


class VehiculoYaDentro(ErrorDominio):
    """La placa tiene un folio abierto: no puede registrarse una segunda entrada."""


class FolioNoEncontrado(ErrorDominio):
    """El folio indicado no existe."""


class FolioYaCerrado(ErrorDominio):
    """El folio ya fue liquidado y cerrado; no admite nuevas operaciones."""


class PagoInsuficiente(ErrorDominio):
    """El monto recibido es menor al importe a cobrar."""


# --- Control de caja --------------------------------------------------------


class TurnoYaAbierto(ErrorDominio):
    """El operador ya tiene un turno abierto; no puede abrir un segundo."""


class SinTurnoAbierto(ErrorDominio):
    """
    No hay turno abierto para cobrar.

    Cobrar sin turno dejaría el ingreso fuera de todo corte: es una fuga de
    control, no una molestia administrativa.
    """


class TurnoYaCerrado(ErrorDominio):
    """El turno ya fue cortado y no admite nuevas operaciones."""


class TurnoConFoliosAbiertos(ErrorDominio):
    """
    Se intenta cerrar un turno que dejó folios sin cobrar.

    Es una advertencia de control: esos vehículos siguen dentro y su cobro
    caería en el turno siguiente, distorsionando ambos cortes.
    """

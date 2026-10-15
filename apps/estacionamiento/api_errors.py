"""
Traducción de errores de dominio a respuestas HTTP.

Centralizar este mapeo cumple dos propósitos:

1. El dominio permanece agnóstico del transporte: no importa `rest_framework`
   ni conoce códigos de estado.
2. El controlador queda libre de bloques `try/except` repetidos. Una regla de
   negocio nueva solo necesita su excepción y una entrada en esta tabla.

Las respuestas exponen el mensaje de negocio —útil para el operador en caja—
pero jamás trazas internas ni detalles de la infraestructura.
"""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as manejador_drf

from .domain.exceptions import (
    ErrorConfiguracion,
    ErrorDominio,
    FolioNoEncontrado,
    FolioYaCerrado,
    PagoInsuficiente,
    SinCajonesDisponibles,
    VehiculoYaDentro,
)

logger = logging.getLogger(__name__)

# 409 Conflict es el código correcto para un estado que impide la operación
# (cajón ocupado, folio ya cerrado); 400 para datos de entrada inválidos.
MAPA_ESTADOS = {
    FolioNoEncontrado: status.HTTP_404_NOT_FOUND,
    VehiculoYaDentro: status.HTTP_409_CONFLICT,
    SinCajonesDisponibles: status.HTTP_409_CONFLICT,
    FolioYaCerrado: status.HTTP_409_CONFLICT,
    PagoInsuficiente: status.HTTP_400_BAD_REQUEST,
    ErrorConfiguracion: status.HTTP_400_BAD_REQUEST,
}


def manejador_excepciones(exc, context):
    """Manejador de excepciones de DRF ampliado con los errores del dominio."""
    if isinstance(exc, ErrorDominio):
        codigo = MAPA_ESTADOS.get(type(exc), status.HTTP_400_BAD_REQUEST)
        logger.warning(
            "Regla de negocio no satisfecha | tipo=%s detalle=%s",
            type(exc).__name__,
            exc,
        )
        return Response(
            {"error": type(exc).__name__, "detalle": str(exc)}, status=codigo
        )
    # Cualquier otra excepción sigue el tratamiento estándar de DRF.
    return manejador_drf(exc, context)

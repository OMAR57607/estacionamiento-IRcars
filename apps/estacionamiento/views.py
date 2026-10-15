"""
Controlador HTTP (capa de interfaz).

Principio rector: **el controlador es delgado**. Su única tarea es
deserializar la petición, invocar el caso de uso correspondiente y serializar
el resultado. No calcula importes, no decide asignación de cajones y no abre
transacciones: todo eso pertenece a `services.py`.

La consecuencia práctica es que añadir mañana un panel de caja en Streamlit o
un controlador de pluma no implica reescribir ninguna regla: ambos invocarán
los mismos servicios.
"""
from __future__ import annotations

from django.db import connection
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Cajon, EstadoFolio, Folio, Tarifa
from .serializers import (
    CajonSerializer,
    CancelacionSerializer,
    EntradaSerializer,
    FolioSerializer,
    SalidaSerializer,
    TarifaSerializer,
)


class TarifaViewSet(viewsets.ModelViewSet):
    """
    Administración del cuadro tarifario.

    Restringido a personal administrativo: el precio es el parámetro más
    sensible del sistema y no debe ser modificable desde una caja.
    """

    queryset = Tarifa.objects.all()
    serializer_class = TarifaSerializer
    permission_classes = [IsAdminUser]


class CajonViewSet(viewsets.ModelViewSet):
    """
    Administración del inventario de cajones.

    Lectura para cualquier usuario autenticado (la caseta necesita consultar
    disponibilidad); escritura solo para personal administrativo.
    """

    queryset = Cajon.objects.all()
    serializer_class = CajonSerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsAdminUser()]

    def get_queryset(self):
        consulta = super().get_queryset()
        tipo = self.request.query_params.get("tipo")
        if tipo:
            # Insensible a mayúsculas: el cliente puede enviar "auto" o "AUTO".
            consulta = consulta.filter(tipo__iexact=tipo)
        activo = self.request.query_params.get("activo")
        if activo is not None:
            consulta = consulta.filter(activo=activo.lower() in {"1", "true", "si", "sí"})
        return consulta


class FolioViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Operación del estacionamiento: entradas, cotizaciones, salidas y cancelaciones.

    No se exponen `update` ni `destroy` de forma deliberada: un folio es un
    registro contable. Las correcciones se realizan por cancelación
    documentada, nunca por edición o borrado, de modo que el histórico sea
    íntegro y auditable.
    """

    serializer_class = FolioSerializer
    permission_classes = [IsAuthenticated]
    # El identificador público es el UUID, no la clave primaria: no revela el
    # volumen de operación ni permite enumerar folios ajenos.
    lookup_field = "uuid"

    def get_queryset(self):
        consulta = Folio.objects.select_related("cajon", "tarifa").all()

        placa = self.request.query_params.get("placa")
        if placa:
            # Búsqueda insensible a mayúsculas y a espacios, tal como la
            # captura el operador al atender una aclaración del cliente.
            consulta = consulta.filter(
                placa__icontains=placa.strip().upper().replace(" ", "")
            )

        estado = self.request.query_params.get("estado")
        if estado:
            consulta = consulta.filter(estado__iexact=estado)

        return consulta

    def create(self, request, *args, **kwargs):
        """POST /folios/ — Registra el ingreso de un vehículo y emite el folio."""
        entrada = EntradaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        folio = services.registrar_entrada(
            placa=entrada.validated_data["placa"],
            tipo_cajon=entrada.validated_data["tipo_cajon"],
            observaciones=entrada.validated_data.get("observaciones", ""),
            operador=request.user if request.user.is_authenticated else None,
        )
        return Response(
            FolioSerializer(folio).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get", "post"])
    def cotizar(self, request, uuid=None):
        """
        GET|POST /folios/{uuid}/cotizar/ — Importe a pagar, sin modificar nada.

        Se expone también en GET porque consultar el monto es una operación
        idempotente y de solo lectura; el operador la ejecuta varias veces
        mientras atiende al cliente.
        """
        folio = services.obtener_folio(uuid)
        cotizacion = services.cotizar_folio(folio)
        return Response(
            {
                "folio": str(folio.uuid),
                "placa": folio.placa,
                "entrada_en": folio.entrada_en,
                "tarifa": folio.tarifa.nombre,
                **cotizacion.desglose(),
            }
        )

    @action(detail=True, methods=["post"])
    def salida(self, request, uuid=None):
        """POST /folios/{uuid}/salida/ — Cobra, cierra el folio y libera el cajón."""
        datos = SalidaSerializer(data=request.data)
        datos.is_valid(raise_exception=True)

        folio, cotizacion = services.registrar_salida(
            uuid_folio=uuid,
            pago_recibido=datos.validated_data.get("pago_recibido"),
            operador=request.user if request.user.is_authenticated else None,
        )
        return Response(
            {
                "folio": FolioSerializer(folio).data,
                "cotizacion": cotizacion.desglose(),
            }
        )

    @action(detail=True, methods=["post"])
    def cancelar(self, request, uuid=None):
        """POST /folios/{uuid}/cancelar/ — Cancelación administrativa documentada."""
        datos = CancelacionSerializer(data=request.data)
        datos.is_valid(raise_exception=True)

        folio = services.cancelar_folio(
            uuid_folio=uuid,
            motivo=datos.validated_data["motivo"],
            operador=request.user if request.user.is_authenticated else None,
        )
        return Response(FolioSerializer(folio).data)


class OcupacionView(APIView):
    """GET /ocupacion/ — Disponibilidad en tiempo real por tipo de cajón."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(services.consultar_ocupacion())


class SaludView(APIView):
    """
    GET /salud/ — Sonda de preparación (readiness) para supervisión.

    A diferencia de `/healthz`, verifica las dependencias: si la base de datos
    no responde, el equipo no está en condiciones de operar y debe retirarse
    del balanceo o alertar al responsable del sitio.
    """

    # Sin autenticación: la sonda la consulta el supervisor del proceso, que no
    # tiene credenciales. No revela información de negocio.
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            base_ok = True
        except Exception:  # pragma: no cover - depende de una falla real de red
            base_ok = False

        folios_abiertos = Folio.objects.filter(estado=EstadoFolio.ABIERTO).count() if base_ok else None
        codigo = status.HTTP_200_OK if base_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(
            {
                "estado": "ok" if base_ok else "degradado",
                "base_de_datos": "ok" if base_ok else "sin_conexion",
                "folios_abiertos": folios_abiertos,
            },
            status=codigo,
        )

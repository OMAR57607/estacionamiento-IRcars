"""Enrutamiento de la API del estacionamiento (versión v1)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CajonViewSet, FolioViewSet, OcupacionView, SaludView, TarifaViewSet

router = DefaultRouter()
router.register("tarifas", TarifaViewSet, basename="tarifa")
router.register("cajones", CajonViewSet, basename="cajon")
router.register("folios", FolioViewSet, basename="folio")

urlpatterns = [
    path("ocupacion/", OcupacionView.as_view(), name="ocupacion"),
    path("salud/", SaludView.as_view(), name="salud"),
    path("", include(router.urls)),
]

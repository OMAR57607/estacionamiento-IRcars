"""
Panel de administración.

Se usa para el alta del cuadro tarifario y del inventario de cajones. Los
folios se exponen en **modo solo lectura**: su edición manual destruiría la
trazabilidad contable. Las correcciones se hacen por el endpoint de
cancelación, que deja constancia del motivo y del operador.
"""
from django.contrib import admin

from .models import Cajon, Folio, Tarifa


@admin.register(Tarifa)
class TarifaAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "cuota_hora",
        "fraccion_minutos",
        "minutos_cortesia",
        "cobro_minimo",
        "tope_diario",
        "activa",
        "vigente_desde",
    )
    list_filter = ("activa",)
    search_fields = ("nombre",)


@admin.register(Cajon)
class CajonAdmin(admin.ModelAdmin):
    list_display = ("codigo", "zona", "tipo", "activo")
    list_filter = ("tipo", "activo", "zona")
    search_fields = ("codigo", "zona")


@admin.register(Folio)
class FolioAdmin(admin.ModelAdmin):
    list_display = (
        "uuid",
        "placa",
        "cajon",
        "estado",
        "entrada_en",
        "salida_en",
        "minutos",
        "importe",
    )
    list_filter = ("estado", "cajon__tipo")
    search_fields = ("placa", "uuid")
    date_hierarchy = "entrada_en"
    # Solo lectura íntegra: ni siquiera el administrador edita un folio.
    readonly_fields = [campo.name for campo in Folio._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

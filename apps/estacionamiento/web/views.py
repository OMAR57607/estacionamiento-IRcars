"""
Vistas de la interfaz web de caja.

Mismo principio que el controlador HTTP de la API: **la vista es delgada**.
Deserializa el formulario, invoca el caso de uso y renderiza. No calcula
importes, no decide asignación de cajones y no abre transacciones.

La consecuencia práctica es que esta interfaz, la API REST y el ejecutable de
escritorio comparten exactamente las mismas reglas de negocio. Un cambio en la
tarificación o en el control de caja se aplica una vez, en `services.py` o en
`domain/`, y los tres lo heredan.
"""
from __future__ import annotations

import csv
from datetime import datetime, time
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .. import services, services_caja
from ..domain.exceptions import ErrorDominio
from ..domain.tarifas import ParametrosTarifa
from ..models import EstadoFolio, EstadoTurno, Folio, Tarifa, Turno
from .forms import (
    AbrirTurnoForm,
    BuscarPlacaForm,
    CancelarFolioForm,
    CerrarTurnoForm,
    CobroForm,
    EntradaForm,
    RangoFechasForm,
    TarifaForm,
)

# Límite de filas mostradas en pantalla. Un reporte de un año en una caseta no
# se lee en el navegador: se exporta. Acotar protege el tiempo de respuesta.
MAXIMO_FILAS = 500


def _es_administrativo(usuario) -> bool:
    return usuario.is_active and usuario.is_staff


solo_administrativo = user_passes_test(_es_administrativo)


def _contexto_base(request) -> dict:
    """
    Datos presentes en todas las pantallas.

    El turno se resuelve contra la base en cada petición, nunca se asume desde
    la sesión: si se cerró desde otra pestaña o dispositivo, un dato en caché
    permitiría cobrar contra un turno ya cortado.
    """
    return {
        "turno_actual": services.obtener_turno_abierto(request.user),
        "es_administrativo": _es_administrativo(request.user),
    }


# ---------------------------------------------------------------------- Caja


@login_required
@require_http_methods(["GET", "POST"])
def caja(request):
    """
    Pantalla principal: entrada, cotización y cobro.

    Las tres operaciones conviven en una sola pantalla porque es lo que el
    operador necesita frente al cliente: cambiar de página entre registrar una
    entrada y cobrar una salida cuesta segundos en cada vehículo.

    La cotización y el cobro son dos pasos separados a propósito: el operador
    informa el monto, recibe el efectivo y solo entonces cierra. Un botón único
    convertiría cualquier error de captura en un folio cerrado e inmutable.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "caja"
    contexto.update(
        {
            "form_entrada": EntradaForm(),
            "form_buscar": BuscarPlacaForm(),
            "form_cobro": None,
            "cotizacion": None,
            "folio_cotizado": None,
        }
    )

    if request.method == "POST":
        accion = request.POST.get("accion")
        try:
            if accion == "entrada":
                return _procesar_entrada(request)
            if accion == "cotizar":
                return _procesar_cotizacion(request, contexto)
            if accion == "cobrar":
                return _procesar_cobro(request)
            if accion == "cancelar":
                return _procesar_cancelacion(request)
            messages.error(request, "Acción no reconocida.")
        except ErrorDominio as exc:
            # Los errores de negocio se muestran tal cual: son informativos y
            # suficientes para que el operador sepa qué hacer.
            messages.error(request, str(exc))

    contexto["patio"] = services.folios_en_patio()
    contexto["ocupacion"] = services.consultar_ocupacion()
    return render(request, "estacionamiento/caja.html", contexto)


def _procesar_entrada(request):
    form = EntradaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revise la placa capturada.")
        return redirect("web:caja")

    folio = services.registrar_entrada(
        placa=form.cleaned_data["placa"],
        tipo_cajon=form.cleaned_data["tipo_cajon"],
        operador=request.user,
    )
    ubicacion = f" · cajón {folio.cajon.codigo}" if folio.cajon else ""
    messages.success(
        request,
        f"Entrada registrada: {folio.placa} a las "
        f"{timezone.localtime(folio.entrada_en):%H:%M}{ubicacion}.",
    )
    # Redirección tras POST: evita que recargar la página registre una segunda
    # entrada de la misma placa.
    return redirect("web:caja")


def _procesar_cotizacion(request, contexto):
    form = BuscarPlacaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Capture la placa a cobrar.")
        return redirect("web:caja")

    folio = services.buscar_folio_abierto_por_placa(form.cleaned_data["placa"])
    if folio is None:
        messages.warning(
            request,
            "No hay ningún vehículo dentro con esa placa. Verifique la captura "
            "o búsquela en la lista de vehículos.",
        )
        return redirect("web:caja")

    cotizacion = services.cotizar_folio(folio)
    # La cotización se renderiza directamente, sin redirección: es una
    # operación de solo lectura y el resultado debe quedar en pantalla junto al
    # formulario de cobro.
    contexto.update(
        {
            "folio_cotizado": folio,
            "cotizacion": cotizacion,
            "form_cobro": CobroForm(initial={"folio": folio.uuid}),
            "patio": services.folios_en_patio(),
            "ocupacion": services.consultar_ocupacion(),
        }
    )
    return render(request, "estacionamiento/caja.html", contexto)


def _procesar_cobro(request):
    form = CobroForm(request.POST)
    if not form.is_valid():
        messages.error(request, "El pago capturado no es una cantidad válida.")
        return redirect("web:caja")

    folio, cotizacion = services.registrar_salida(
        uuid_folio=form.cleaned_data["folio"],
        pago_recibido=form.cleaned_data.get("pago_recibido"),
        operador=request.user,
        # La interfaz de caja sí maneja efectivo: cobrar sin turno abierto
        # dejaría el ingreso fuera de todo corte.
        exigir_turno=True,
    )

    if cotizacion.es_cortesia:
        messages.success(
            request, f"Salida de {folio.placa} registrada en cortesía (sin cobro)."
        )
    elif folio.cambio is not None and folio.cambio > 0:
        messages.success(
            request,
            f"Cobrado ${folio.importe:,.2f} de {folio.placa}. "
            f"CAMBIO A ENTREGAR: ${folio.cambio:,.2f}",
        )
    else:
        messages.success(request, f"Cobrado ${folio.importe:,.2f} de {folio.placa}.")
    return redirect("web:caja")


def _procesar_cancelacion(request):
    form = CancelarFolioForm(request.POST)
    if not form.is_valid():
        messages.error(request, "La cancelación requiere un motivo documentado.")
        return redirect("web:caja")

    folio = services.cancelar_folio(
        uuid_folio=form.cleaned_data["folio"],
        motivo=form.cleaned_data["motivo"],
        operador=request.user,
    )
    messages.warning(request, f"Folio de {folio.placa} cancelado.")
    return redirect("web:caja")


@login_required
def cancelar(request, uuid):
    """Pantalla de confirmación de cancelación, con captura del motivo."""
    contexto = _contexto_base(request)
    contexto["seccion"] = "caja"
    folio = services.obtener_folio(uuid)
    contexto.update(
        {"folio": folio, "form": CancelarFolioForm(initial={"folio": folio.uuid})}
    )
    return render(request, "estacionamiento/cancelar.html", contexto)


# --------------------------------------------------------------------- Turno


@login_required
@require_http_methods(["GET", "POST"])
def turno(request):
    """
    Apertura de caja y corte ciego.

    Mientras el turno está abierto, esta pantalla muestra el avance operativo
    —folios cobrados, vehículos dentro— pero **nunca** el total de efectivo
    esperado. Esa omisión es el control completo: el conteo físico debe ser
    independiente de la cifra del sistema.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "turno"
    turno_actual = contexto["turno_actual"]

    if request.method == "POST":
        try:
            if request.POST.get("accion") == "abrir":
                form = AbrirTurnoForm(request.POST)
                if form.is_valid():
                    nuevo = services_caja.abrir_turno(
                        operador=request.user,
                        fondo_inicial=form.cleaned_data["fondo_inicial"],
                    )
                    messages.success(
                        request,
                        f"Turno {nuevo.pk} abierto con fondo de "
                        f"${nuevo.fondo_inicial:,.2f}. Ya puede cobrar salidas.",
                    )
                    return redirect("web:turno")
                messages.error(request, "Capture un fondo inicial válido.")

            elif request.POST.get("accion") == "cerrar":
                if turno_actual is None:
                    messages.error(request, "No tiene un turno abierto que cortar.")
                    return redirect("web:turno")
                form = CerrarTurnoForm(request.POST)
                if form.is_valid():
                    cerrado, resumen = services_caja.cerrar_turno(
                        turno_id=turno_actual.pk,
                        efectivo_declarado=form.cleaned_data["efectivo_declarado"],
                        observaciones=form.cleaned_data.get("observaciones", ""),
                    )
                    # El resultado se pasa por la URL del comprobante en lugar
                    # de un mensaje: el corte merece una pantalla propia, con
                    # el desglose completo e imprimible.
                    return redirect("web:corte", turno_id=cerrado.pk)
                messages.error(request, "Capture el efectivo contado.")
        except ErrorDominio as exc:
            messages.error(request, str(exc))

    if turno_actual is not None:
        contexto["avance"] = services_caja.resumen_operativo(turno_actual)
        contexto["form_cerrar"] = CerrarTurnoForm()
    else:
        contexto["form_abrir"] = AbrirTurnoForm()

    contexto["turnos_cerrados"] = (
        Turno.objects.select_related("operador")
        .filter(estado=EstadoTurno.CERRADO)
        .order_by("-cerrado_en")[:50]
    )
    return render(request, "estacionamiento/turno.html", contexto)


@login_required
def corte(request, turno_id: int):
    """
    Comprobante del corte, ya con la diferencia revelada.

    Pantalla separada y con estilo de impresión: el corte se firma en papel y
    se entrega junto con el efectivo.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "turno"
    turno_consultado = (
        Turno.objects.select_related("operador").filter(pk=turno_id).first()
    )
    if turno_consultado is None:
        messages.error(request, f"No existe el turno {turno_id}.")
        return redirect("web:turno")

    # Un operador solo consulta sus propios cortes; el perfil administrativo
    # consulta cualquiera. Un corte ajeno contiene cifras de efectivo que no
    # le corresponden.
    if turno_consultado.operador_id != request.user.pk and not _es_administrativo(
        request.user
    ):
        messages.error(request, "No tiene permiso para consultar ese corte.")
        return redirect("web:turno")

    contexto["turno_corte"] = turno_consultado
    contexto["resumen"] = services_caja.calcular_resumen(turno_consultado)
    contexto["folios"] = (
        Folio.objects.filter(turno=turno_consultado, estado=EstadoFolio.CERRADO)
        .order_by("salida_en")[:MAXIMO_FILAS]
    )
    return render(request, "estacionamiento/corte.html", contexto)


# ------------------------------------------------------------------ Reportes


@login_required
@solo_administrativo
def reportes(request):
    """Reporte administrativo por periodo, con exportación a CSV."""
    contexto = _contexto_base(request)
    contexto["seccion"] = "reportes"

    form = RangoFechasForm(request.GET or RangoFechasForm.valores_por_omision())
    contexto["form"] = form

    if not form.is_valid():
        return render(request, "estacionamiento/reportes.html", contexto)

    desde, hasta = _rango_local(form.cleaned_data["desde"], form.cleaned_data["hasta"])

    try:
        contexto["datos"] = services_caja.reporte_por_rango(desde, hasta)
    except ErrorDominio as exc:
        messages.error(request, str(exc))
        return render(request, "estacionamiento/reportes.html", contexto)

    folios = (
        Folio.objects.select_related("operador_salida")
        .filter(estado=EstadoFolio.CERRADO, salida_en__gte=desde, salida_en__lte=hasta)
        .order_by("-salida_en")
    )

    if request.GET.get("formato") == "csv":
        return _exportar_csv(folios[:5000])

    contexto["folios"] = folios[:MAXIMO_FILAS]
    contexto["total_folios"] = folios.count()
    contexto["truncado"] = contexto["total_folios"] > MAXIMO_FILAS
    return render(request, "estacionamiento/reportes.html", contexto)


def _rango_local(desde, hasta) -> tuple[datetime, datetime]:
    """
    Convierte dos fechas en un intervalo que cubre ambos días completos.

    El límite superior es 23:59:59.999999 del día indicado: un reporte «del 1
    al 5» debe incluir todo el día 5, no detenerse a medianoche del 4. La zona
    horaria es la local configurada, porque el corte de día es el del negocio,
    no el de UTC.
    """
    zona = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(desde, time.min), zona),
        timezone.make_aware(datetime.combine(hasta, time.max), zona),
    )


def _exportar_csv(folios) -> HttpResponse:
    """
    Exporta los folios a CSV.

    Se usa `utf-8-sig` porque Excel en Windows requiere la marca de orden de
    bytes para interpretar los acentos; sin ella el contador ve texto corrupto
    y supone que el archivo está dañado.
    """
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    nombre = f"folios_{timezone.localtime():%Y%m%d_%H%M}.csv"
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
    respuesta.write("﻿")

    escritor = csv.writer(respuesta)
    escritor.writerow(
        [
            "folio",
            "placa",
            "entrada",
            "salida",
            "minutos",
            "importe",
            "pago_recibido",
            "cambio",
            "cuota_hora_aplicada",
            "operador_salida",
            "turno",
        ]
    )
    for folio in folios:
        escritor.writerow(
            [
                folio.uuid,
                folio.placa,
                timezone.localtime(folio.entrada_en).strftime("%d/%m/%Y %H:%M"),
                timezone.localtime(folio.salida_en).strftime("%d/%m/%Y %H:%M")
                if folio.salida_en
                else "",
                folio.minutos,
                folio.importe,
                folio.pago_recibido if folio.pago_recibido is not None else "",
                folio.cambio if folio.cambio is not None else "",
                folio.tarifa_cuota_hora,
                folio.operador_salida or "",
                folio.turno_id or "",
            ]
        )
    return respuesta


# ------------------------------------------------------------------- Tarifa


@login_required
@require_http_methods(["GET", "POST"])
def tarifa(request):
    """
    Consulta del cuadro tarifario y alta de tarifas nuevas.

    La consulta está abierta a cualquier operador —necesita saber qué cobra—,
    pero el alta exige perfil administrativo: el precio es el parámetro más
    sensible del sistema y modificarlo desde una caja sería la vía más simple
    de cobrar de menos y quedarse con la diferencia.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "tarifa"

    if request.method == "POST":
        if not _es_administrativo(request.user):
            messages.error(
                request,
                "Solo el perfil administrativo puede modificar el cuadro tarifario.",
            )
            return redirect("web:tarifa")

        form = TarifaForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            try:
                # Validación del dominio antes de persistir: una fracción
                # inválida produciría cobros erróneos de forma silenciosa.
                ParametrosTarifa(
                    cuota_hora=datos["cuota_hora"],
                    minutos_cortesia=datos["minutos_cortesia"],
                    fraccion_minutos=datos["fraccion_minutos"],
                    cobro_minimo=datos["cobro_minimo"],
                    tope_diario=datos.get("tope_diario"),
                )
            except ErrorDominio as exc:
                messages.error(request, str(exc))
                contexto["form"] = form
                contexto["tarifas"] = Tarifa.objects.order_by("-vigente_desde")[:50]
                return render(request, "estacionamiento/tarifa.html", contexto)

            ahora = timezone.now()
            Tarifa.objects.filter(activa=True).update(activa=False)
            nueva = Tarifa.objects.create(
                nombre=f"{datos['nombre']} ({ahora:%Y-%m-%d %H:%M})",
                cuota_hora=datos["cuota_hora"],
                minutos_cortesia=datos["minutos_cortesia"],
                fraccion_minutos=datos["fraccion_minutos"],
                cobro_minimo=datos["cobro_minimo"],
                tope_diario=datos.get("tope_diario"),
                activa=True,
                vigente_desde=ahora,
            )
            messages.success(
                request,
                f"Tarifa «{nueva.nombre}» vigente desde ahora. La anterior se "
                "desactivó pero se conserva en el histórico: los folios ya "
                "cobrados mantienen el precio con que se calcularon.",
            )
            return redirect("web:tarifa")
        messages.error(request, "Revise los datos de la tarifa.")
        contexto["form"] = form
    else:
        contexto["form"] = TarifaForm()

    try:
        contexto["vigente"] = services.obtener_tarifa_vigente()
    except ErrorDominio:
        contexto["vigente"] = None

    contexto["tarifas"] = Tarifa.objects.order_by("-vigente_desde")[:50]
    return render(request, "estacionamiento/tarifa.html", contexto)

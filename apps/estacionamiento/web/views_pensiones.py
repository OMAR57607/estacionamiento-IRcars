"""
Vistas de la pensión diaria.

Mismo principio que el resto de la interfaz: la vista es delgada. Valida la
forma, llama al caso de uso de `services_pensiones` y dibuja. No calcula
periodos ni montos: los dos salen del dominio, de modo que lo que la pantalla
informa es exactamente lo que se cobra.

Viven aparte de `views.py` porque ese archivo ya pasa de mil seiscientas
líneas, y la pensión es un módulo con sus propias pantallas.

Permisos (docs/REDISENO.md §2, D7): cualquier operador da de alta y cobra; el
perfil administrativo cambia el precio, da de baja y cancela pagos, siempre
con su contraseña.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .. import services, services_caja, services_pensiones
from ..domain.exceptions import ErrorDominio
from ..domain.pensiones import MAXIMO_DIAS
from ..models import EstadoPagoPension, EstadoPension, PagoPension, Pension
from .forms import (
    DIAS_FRECUENTES,
    CobroPensionForm,
    ConfirmarClaveForm,
    DatosPensionForm,
    MotivoConClaveForm,
    PensionForm,
    PrecioPensionForm,
)
from .views import (
    _atajos_para,
    _contexto_base,
    _es_administrativo,
    solo_administrativo,
)

logger = logging.getLogger(__name__)

FILTROS = (
    ("", "Todas"),
    ("POR_VENCER", "Por vencer"),
    ("VENCIDA", "Vencidas"),
    ("SIN_PAGO", "Sin pago"),
    ("BAJA", "Bajas"),
)

POR_PAGINA = 50


def _contexto(request) -> dict:
    contexto = _contexto_base(request)
    contexto["seccion"] = "pensiones"
    return contexto


def _clave_correcta(request, clave: str) -> bool:
    return bool(authenticate(username=request.user.get_username(), password=clave))


# --- Lista -------------------------------------------------------------------


@login_required
@require_safe
def lista(request):
    """
    Pensiones con su vigencia, primero las que requieren atención.

    La búsqueda va por GET y la resuelve el servidor: funciona sin JavaScript
    y el enlace con la búsqueda se puede guardar.
    """
    contexto = _contexto(request)
    texto = request.GET.get("q", "").strip()
    filtro = request.GET.get("estado", "")
    if filtro not in dict(FILTROS):
        filtro = ""

    filas = services_pensiones.listar(texto=texto, filtro=filtro)
    try:
        pagina = max(1, int(request.GET.get("pagina", "1")))
    except ValueError:
        pagina = 1
    inicio = (pagina - 1) * POR_PAGINA

    contexto.update(
        {
            "filas": filas[inicio : inicio + POR_PAGINA],
            "total": len(filas),
            "pagina": pagina,
            "hay_siguiente": len(filas) > inicio + POR_PAGINA,
            "texto": texto,
            "filtro": filtro,
            "filtros": FILTROS,
            "conteos": services_pensiones.conteos(),
            "plan": services_pensiones.plan_vigente_o_nada(),
        }
    )
    return render(request, "estacionamiento/pensiones/lista.html", contexto)


# --- Alta --------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def alta(request):
    contexto = _contexto(request)
    contexto["plan"] = services_pensiones.plan_vigente_o_nada()
    contexto["aviso_privacidad_url"] = getattr(settings, "IRCARS_AVISO_PRIVACIDAD_URL", "")
    inicial = {}
    if request.GET.get("placa"):
        inicial["placa"] = request.GET["placa"]
    form = PensionForm(request.POST or None, initial=inicial)

    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            pension = services_pensiones.registrar_pension(
                placa=datos["placa"],
                tipo_vehiculo=datos["tipo_vehiculo"],
                nombre_cliente=datos["nombre_cliente"],
                telefono=datos["telefono"],
                descripcion_vehiculo=datos["descripcion_vehiculo"],
                notas=datos["notas"],
                operador=request.user,
            )
        except ErrorDominio as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Pensión de {pension.placa} registrada.")
            if request.POST.get("accion") == "cobrar":
                return redirect("web:pension_cobrar", pk=pension.pk)
            return redirect("web:pension", pk=pension.pk)

    contexto["form"] = form
    return render(
        request,
        "estacionamiento/pensiones/alta.html",
        contexto,
        status=400 if request.method == "POST" else 200,
    )


# --- Ficha -------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def ficha(request, pk: int):
    """
    Todo lo de una pensión: vigencia, pagos, estancias y acciones.

    La corrección de datos de contacto se hace aquí mismo, por POST, y queda
    reservada al perfil administrativo junto con la baja y la cancelación.
    """
    pension = get_object_or_404(Pension, pk=pk)
    contexto = _contexto(request)

    if request.method == "POST":
        if not _es_administrativo(request.user):
            messages.error(request, "Solo el perfil administrativo corrige los datos.")
            return redirect("web:pension", pk=pk)
        form_datos = DatosPensionForm(request.POST)
        if form_datos.is_valid():
            try:
                services_pensiones.editar_datos(pension_id=pk, **form_datos.cleaned_data)
            except ErrorDominio as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Datos de la pensión corregidos.")
                return redirect("web:pension", pk=pk)
        contexto["form_datos"] = form_datos
    else:
        contexto["form_datos"] = DatosPensionForm(
            initial={
                "nombre_cliente": pension.nombre_cliente,
                "telefono": pension.telefono,
                "descripcion_vehiculo": pension.descripcion_vehiculo,
                "notas": pension.notas,
            }
        )

    pagos = list(pension.pagos.select_related("operador").order_by("-creado_en")[:50])
    ultimo_vigente = next(
        (p for p in sorted(pagos, key=lambda p: (p.periodo_hasta, p.pk), reverse=True)
         if p.estado == EstadoPagoPension.VIGENTE),
        None,
    )
    contexto.update(
        {
            "pension": pension,
            "vigencia": services_pensiones.vigencia(pension),
            "pagos": pagos,
            "cancelable": (
                ultimo_vigente
                if ultimo_vigente and ultimo_vigente.turno.estado == "ABIERTO"
                else None
            ),
            "estancias": pension.folios.order_by("-entrada_en")[:10],
            "dentro": services_pensiones.folio_abierto_de(pension),
            # Dos formularios iguales en la misma página: cada uno con su
            # prefijo de identificador, para que las etiquetas no apunten al
            # campo del otro. Los nombres de los campos no cambian.
            "form_cancelar": MotivoConClaveForm(auto_id="cancelar_%s"),
            "form_baja": MotivoConClaveForm(auto_id="baja_%s"),
            "activa": pension.estado == EstadoPension.ACTIVA,
        }
    )
    return render(request, "estacionamiento/pensiones/ficha.html", contexto)


@login_required
@solo_administrativo
@require_POST
def baja(request, pk: int):
    form = MotivoConClaveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "La baja requiere el motivo y su contraseña.")
        return redirect("web:pension", pk=pk)
    if not _clave_correcta(request, form.cleaned_data["clave"]):
        messages.error(request, "Contraseña incorrecta. La pensión sigue activa.")
        return redirect("web:pension", pk=pk)
    try:
        pension = services_pensiones.dar_de_baja(
            pension_id=pk, motivo=form.cleaned_data["motivo"], operador=request.user
        )
    except ErrorDominio as exc:
        messages.error(request, str(exc))
        return redirect("web:pension", pk=pk)
    messages.warning(
        request,
        f"Pensión de {pension.placa} dada de baja. No se reembolsa nada; la "
        "placa queda libre para un alta futura.",
    )
    return redirect("web:pension", pk=pk)


@login_required
@solo_administrativo
@require_POST
def cancelar_pago(request, pago_id: int):
    pago = get_object_or_404(PagoPension, pk=pago_id)
    form = MotivoConClaveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "La cancelación requiere el motivo y su contraseña.")
    elif not _clave_correcta(request, form.cleaned_data["clave"]):
        messages.error(request, "Contraseña incorrecta. El pago sigue vigente.")
    else:
        try:
            services_pensiones.cancelar_pago(
                pago_id=pago.pk, motivo=form.cleaned_data["motivo"], operador=request.user
            )
        except ErrorDominio as exc:
            messages.error(request, str(exc))
        else:
            messages.warning(
                request,
                f"Pago de ${pago.monto:,.2f} cancelado. Devuelva el efectivo: ya no "
                "cuenta en el corte del turno.",
            )
    return redirect("web:pension", pk=pago.pension_id)


# --- Cobro -------------------------------------------------------------------


def _dias_pedidos(valor) -> int:
    try:
        dias = int(valor)
    except (TypeError, ValueError):
        return 1
    return min(max(dias, 1), MAXIMO_DIAS)


@login_required
@require_http_methods(["GET", "POST"])
def cobrar(request, pk: int):
    """
    Cobro de días de pensión, en dos pasos como la caja.

    El GET informa el periodo y el monto; el POST cobra y lleva al recibo.
    Elegir los días son enlaces y no un formulario con JavaScript: cada uno
    vuelve a pedir la página con el periodo recalculado por el servidor.
    """
    pension = get_object_or_404(Pension, pk=pk)
    contexto = _contexto(request)

    if pension.estado != EstadoPension.ACTIVA:
        messages.error(request, f"La pensión de {pension.placa} está dada de baja.")
        return redirect("web:pension", pk=pk)

    if request.method == "POST":
        form = CobroPensionForm(request.POST)
        if form.is_valid():
            try:
                pago = services_pensiones.cobrar_pension(
                    pension_id=pension.pk,
                    dias=form.cleaned_data["dias"],
                    pago_recibido=form.cleaned_data.get("pago_recibido"),
                    operador=request.user,
                )
            except ErrorDominio as exc:
                messages.error(request, str(exc))
            else:
                # Redirección tras POST: recargar el recibo no cobra dos veces.
                return redirect("web:pension_recibo", uuid=pago.uuid)
        else:
            messages.error(request, "Revise los días y el pago capturado.")
        dias = _dias_pedidos(request.POST.get("dias"))
    else:
        dias = _dias_pedidos(request.GET.get("dias", 1))
        form = CobroPensionForm(initial={"dias": dias})

    try:
        cotizacion = services_pensiones.cotizar_pago(pension, dias=dias)
    except ErrorDominio as exc:
        messages.error(request, str(exc))
        return redirect("web:pension", pk=pk)

    form.fields["pago_recibido"].widget.attrs["data-importe"] = str(cotizacion["monto"])
    form.initial["dias"] = dias
    contexto.update(
        {
            "pension": pension,
            "vigencia": services_pensiones.vigencia(pension),
            "cotizacion": cotizacion,
            "form": form,
            "dias": dias,
            "opciones_dias": DIAS_FRECUENTES,
            "atajos": _atajos_para(cotizacion["monto"]),
            "puede_cobrar": contexto["turno_actual"] is not None
            and not services_caja.exige_cierre_antes_de_cobrar(contexto["turno_actual"]),
        }
    )
    return render(request, "estacionamiento/pensiones/cobrar.html", contexto)


@login_required
@require_safe
def recibo(request, uuid):
    """
    Recibo imprimible del pago.

    Lo que el cliente puede pedir ante una aclaración: qué periodo pagó, a qué
    precio, cuánto entregó y quién le cobró.
    """
    pago = get_object_or_404(
        PagoPension.objects.select_related("pension", "operador"), uuid=uuid
    )
    contexto = _contexto(request)
    contexto["pago"] = pago
    return render(request, "estacionamiento/pensiones/recibo.html", contexto)


# --- Precio ------------------------------------------------------------------


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def precio(request):
    """
    Cambio del precio de la pensión, con contraseña en el mismo envío.

    Es una operación de una vez al año: un solo formulario con el precio
    nuevo y la contraseña basta, sin pantalla de desbloqueo. Los pagos ya
    hechos conservan su precio; el nuevo rige desde el siguiente cobro.
    """
    contexto = _contexto(request)
    contexto["seccion"] = "tarifa"
    contexto["plan"] = services_pensiones.plan_vigente_o_nada()
    form = PrecioPensionForm(
        request.POST or None,
        initial={"precio_diario": contexto["plan"].precio_diario if contexto["plan"] else None},
    )
    form_clave = ConfirmarClaveForm(request.POST or None)

    if request.method == "POST":
        if not form.is_valid():
            messages.error(request, "Revise el precio capturado.")
        elif not form_clave.is_valid() or not _clave_correcta(
            request, form_clave.cleaned_data["clave"]
        ):
            messages.error(request, "Contraseña incorrecta. El precio no cambió.")
        else:
            plan = services_pensiones.cambiar_precio(
                precio_diario=form.cleaned_data["precio_diario"], operador=request.user
            )
            messages.success(
                request,
                f"La pensión quedó en ${plan.precio_diario:,.2f} por día. Los pagos "
                "ya hechos conservan el precio con que se cobraron.",
            )
            return redirect("web:tarifa")

    contexto["form"] = form
    contexto["form_clave"] = ConfirmarClaveForm()
    return render(request, "estacionamiento/pensiones/precio.html", contexto)

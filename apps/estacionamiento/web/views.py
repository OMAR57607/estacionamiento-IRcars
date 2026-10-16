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
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_safe

from .. import services, services_caja
from ..domain.exceptions import ErrorDominio
from ..domain.tarifas import ParametrosTarifa, cotizar
from ..models import EstadoFolio, EstadoTurno, Folio, Tarifa, Turno
from .forms import (
    AbrirTurnoForm,
    ClaveOperadorForm,
    BuscarPlacaForm,
    CancelarFolioForm,
    CerrarTurnoForm,
    CobroForm,
    DesbloqueoPrecioForm,
    EntradaForm,
    OperadorEditarForm,
    OperadorNuevoForm,
    RangoFechasForm,
    TarifaForm,
)

# Límite de filas mostradas en pantalla. Un reporte de un año en una caseta no
# se lee en el navegador: se exporta. Acotar protege el tiempo de respuesta.
MAXIMO_FILAS = 500

logger = logging.getLogger(__name__)


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


class CambiarClave(PasswordChangeView):
    """
    Cambio de la propia contraseña, dentro de la aplicación.

    Existe porque no hay otra vía: el panel de administración de Django no se
    monta, de modo que sin esta pantalla la contraseña de una cuenta quedaría
    fijada para siempre al valor con que se creó —y la del primer operador se
    crea desde una variable de entorno del servidor, conocida por quien hizo
    la instalación—.

    `PasswordChangeView` exige la contraseña actual y aplica los validadores
    configurados, así que no hace falta escribir nada de eso: lo que aporta
    esta subclase es la plantilla propia y el destino al terminar.
    """

    template_name = "estacionamiento/clave.html"
    success_url = reverse_lazy("web:caja")

    def form_valid(self, form):
        messages.success(
            self.request,
            "Contraseña actualizada. Su sesión sigue abierta en este equipo.",
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        # La barra de navegación necesita el turno y el perfil del operador,
        # igual que en el resto de las pantallas.
        contexto = super().get_context_data(**kwargs)
        contexto.update(_contexto_base(self.request))
        return contexto


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
    # Se va al boleto y no de vuelta a la caja porque este es el único momento
    # en que el cliente está delante: es cuando puede escanear el código o
    # recibir el enlace. Pedirle que espere a que el operador navegue a otra
    # pantalla equivale a que no lo haga.
    #
    # Sigue siendo una redirección tras POST, de modo que recargar no registra
    # una segunda entrada de la misma placa.
    return redirect("web:boleto", uuid=folio.uuid)


@login_required
@require_safe
def boleto(request, uuid):
    """
    Boleto recién emitido, para enseñárselo al cliente o enviárselo.

    Es la pantalla que el dependiente pone delante del cliente: un código QR
    grande que se escanea desde el teléfono del propio cliente, y un botón
    para mandarle el enlace por WhatsApp si prefiere recibirlo. Son las dos
    formas en que un boleto llega hoy a alguien, y conviene que convivan: la
    cámara falla con poca luz o con la pantalla sucia, y el mensaje no.

    La dirección se construye con `build_absolute_uri` y no con una variable
    de configuración. Así el enlace del QR es siempre el del servidor que
    atiende la petición, sin que nadie tenga que mantener un dominio escrito
    en dos sitios —y sin que un despliegue nuevo emita códigos que apuntan al
    anterior—.

    Solo lectura: emitir el folio ya ocurrió. Si esta vista aceptara un POST,
    recargar la pantalla del boleto podría repetir una operación con efecto
    económico.
    """
    folio = get_object_or_404(Folio, uuid=uuid)

    url_publica = request.build_absolute_uri(
        reverse("boletos:publico", kwargs={"uuid_folio": folio.uuid})
    )

    # El mensaje no lleva la placa. El enlace es lo único que el cliente
    # necesita, y lo que no se escribe en un mensaje no viaja por WhatsApp.
    texto = (
        f"Su boleto de estacionamiento en "
        f"{getattr(settings, 'IRCARS_NOMBRE_COMERCIAL', 'nuestro estacionamiento')}: "
        f"{url_publica}"
    )

    contexto = _contexto_base(request)
    contexto.update(
        {
            "seccion": "caja",
            "folio": folio,
            "url_publica": url_publica,
            "url_whatsapp": f"https://wa.me/?{urlencode({'text': texto})}",
            "texto_compartir": texto,
            "qr": _qr_o_nada(url_publica),
        }
    )
    return render(request, "estacionamiento/boleto.html", contexto)


def _qr_o_nada(url: str):
    """
    Dibuja el código, o devuelve `None` si no se puede.

    Un fallo del generador no debe dejar al operador sin pantalla: el folio ya
    está emitido y el enlace sigue sirviendo para dictarlo o compartirlo. La
    plantilla muestra un aviso en su lugar.
    """
    from apps.boletos.qr import QRNoDisponible, svg_qr

    try:
        return svg_qr(url, escala=6)
    except QRNoDisponible:
        logger.warning("No se pudo generar el código QR del boleto.")
        return None


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


# -------------------------------------------------------------------- Precio
#
# El precio es un dato que se configura una vez y se deja quieto. Esta sección
# está construida sobre esa idea, que antes no cumplía: la pantalla era un
# formulario de seis campos siempre abiertos con un botón de guardar, de modo
# que cada visita invitaba a cambiar la tarifa, y los campos arrancaban con
# valores fijos de ejemplo —no con los vigentes—, así que «subir un peso la
# hora» reescribía en silencio la cortesía, la fracción y el tope.
#
# Ahora el precio está bloqueado: `tarifa` lo muestra en solo lectura y
# `tarifa_cambiar` exige confirmar la contraseña antes de abrir el formulario.
# Es el mismo recorrido que el ejecutable de escritorio, y por el mismo motivo:
# tres pasos para una operación que se hace una vez al año, y ninguno para la
# que se hace cien veces al día.

# Permanencias de muestra para la tabla de ejemplos. Cubren el rango real de
# una caseta: la maniobra, la gestión corta, la jornada y el día completo. Los
# cinco parámetros del cuadro tarifario no se entienden leyéndolos —«fracción
# de cobro 1 min» no le dice nada a nadie—; ver que una hora cuesta $30 y que
# media cuesta $15 sí, y permite comprobar el precio sin hacer cuentas a mano.
EJEMPLOS_PERMANENCIA = (
    (15, "15 minutos"),
    (30, "Media hora"),
    (60, "1 hora"),
    (120, "2 horas"),
    (480, "8 horas"),
    (1440, "24 horas"),
)

# Vigencia del desbloqueo, en minutos. Alcanza para repasar los ejemplos y
# guardar, y no tanto como para que una pestaña quede abierta y desbloqueada
# media jornada en el monitor de la caseta.
MINUTOS_DESBLOQUEO = 5

CLAVE_DESBLOQUEO = "precio_desbloqueado_en"

# Punto de partida de la primera configuración, cuando todavía no existe
# ningún precio que respetar. Fuera de ese caso, los valores iniciales del
# formulario salen siempre del precio vigente.
PRECIO_DE_ARRANQUE = {
    "cuota_hora": Decimal("30.00"),
    "minutos_cortesia": 15,
    "fraccion_minutos": 1,
    "cobro_minimo": Decimal("0.00"),
    "tope_diario": Decimal("250.00"),
}


# ---------------------------------------------------------------- Operadores
#
# Esta pantalla existe porque el panel de administración de Django no se monta:
# sin ella, dar de alta a alguien exigiría tocar variables de entorno y
# desplegar, y cambiar un nombre de usuario sería imposible.
#
# Lo que más condiciona su diseño es que **no hay red de seguridad**. Si el
# último administrador se quita los permisos o se desactiva, nadie puede
# devolvérselos: no hay panel desde donde rescatar la instalación. De ahí que
# las comprobaciones de abajo no sean advertencias sino impedimentos.


def _es_ultimo_administrativo(usuario) -> bool:
    """Indica si quitarle los permisos dejaría la instalación sin gobierno."""
    Usuario = get_user_model()
    otros = Usuario.objects.filter(is_staff=True, is_active=True).exclude(pk=usuario.pk)
    return not otros.exists()


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def operadores(request):
    """
    Listado y alta de operadores.

    Restringida a los administrativos: quien puede crear cuentas puede crear
    una cuenta que cobre, y eso tiene el mismo peso que cambiar el precio.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "operadores"
    form = OperadorNuevoForm()

    if request.method == "POST":
        form = OperadorNuevoForm(request.POST)
        if form.is_valid():
            creado = get_user_model().objects.create_user(
                username=form.cleaned_data["usuario"],
                password=form.cleaned_data["clave"],
                is_staff=form.cleaned_data["administrativo"],
            )
            logger.info(
                "Operador creado | usuario=%s administrativo=%s por=%s",
                creado.username,
                creado.is_staff,
                request.user.get_username(),
            )
            messages.success(
                request,
                f"Operador «{creado.username}» creado. Pídale que cambie su "
                "contraseña la primera vez que entre.",
            )
            return redirect("web:operadores")
        messages.error(request, "Revise los datos del operador.")

    contexto["form"] = form
    contexto["operadores"] = _listado_de_operadores()
    return render(request, "estacionamiento/operadores.html", contexto)


def _listado_de_operadores() -> list[dict]:
    """
    Operadores con lo que hace falta para decidir sobre ellos.

    El turno abierto se incluye porque condiciona la desactivación: apagar a
    quien tiene caja abierta dejaría ese efectivo sin corte y sin responsable.
    """
    Usuario = get_user_model()
    filas = []
    for usuario in Usuario.objects.order_by("-is_active", "username"):
        filas.append(
            {
                "objeto": usuario,
                "turno_abierto": services.obtener_turno_abierto(usuario),
                "es_uno_mismo": False,
            }
        )
    return filas


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def operador(request, pk: int):
    """
    Ficha de un operador: nombre, permisos, estado y contraseña.

    Nótese lo que **no** ofrece: borrar. No es una omisión. Los folios y los
    turnos referencian al operador con `PROTECT`, de modo que un operador con
    historial no puede eliminarse sin romper la trazabilidad de movimientos
    con efecto económico. Lo que corresponde es desactivarlo: deja de entrar y
    su historial permanece atribuido.
    """
    objetivo = get_object_or_404(get_user_model(), pk=pk)
    contexto = _contexto_base(request)
    contexto["seccion"] = "operadores"

    form = OperadorEditarForm(
        operador=objetivo,
        initial={
            "usuario": objetivo.get_username(),
            "administrativo": objetivo.is_staff,
        },
    )
    form_clave = ClaveOperadorForm()

    if request.method == "POST":
        accion = request.POST.get("accion")
        try:
            if accion == "datos":
                return _guardar_datos_operador(request, objetivo)
            if accion == "clave":
                return _restablecer_clave_operador(request, objetivo)
            if accion == "estado":
                return _cambiar_estado_operador(request, objetivo)
            messages.error(request, "Acción no reconocida.")
        except ErrorDeGobierno as exc:
            messages.error(request, str(exc))
        return redirect("web:operador", pk=objetivo.pk)

    contexto.update(
        {
            "objetivo": objetivo,
            "form": form,
            "form_clave": form_clave,
            "turno_abierto": services.obtener_turno_abierto(objetivo),
            "es_uno_mismo": objetivo.pk == request.user.pk,
            "es_ultimo_administrativo": _es_ultimo_administrativo(objetivo),
        }
    )
    return render(request, "estacionamiento/operador.html", contexto)


class ErrorDeGobierno(Exception):
    """Impedimento que protege el acceso a la propia instalación."""


def _guardar_datos_operador(request, objetivo):
    form = OperadorEditarForm(request.POST, operador=objetivo)
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return redirect("web:operador", pk=objetivo.pk)

    administrativo = form.cleaned_data["administrativo"]

    # Quitarse a uno mismo los permisos, siendo el último administrativo, deja
    # la instalación sin nadie que pueda devolvérselos: no hay panel de
    # administración desde donde rescatarla.
    if (
        objetivo.is_staff
        and not administrativo
        and _es_ultimo_administrativo(objetivo)
    ):
        raise ErrorDeGobierno(
            "No puede quitarse el permiso administrativo al último operador "
            "que lo tiene: nadie podría volver a concederlo. Primero "
            "concédaselo a otra persona."
        )

    anterior = objetivo.get_username()
    objetivo.username = form.cleaned_data["usuario"]
    objetivo.is_staff = administrativo
    objetivo.save(update_fields=["username", "is_staff"])

    logger.info(
        "Operador modificado | de=%s a=%s administrativo=%s por=%s",
        anterior,
        objetivo.username,
        objetivo.is_staff,
        request.user.get_username(),
    )
    if anterior != objetivo.username:
        messages.success(
            request,
            f"«{anterior}» pasa a llamarse «{objetivo.username}». Su historial "
            "y sus cortes siguen siendo suyos.",
        )
    else:
        messages.success(request, "Datos del operador actualizados.")
    return redirect("web:operador", pk=objetivo.pk)


def _restablecer_clave_operador(request, objetivo):
    form = ClaveOperadorForm(request.POST)
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return redirect("web:operador", pk=objetivo.pk)

    objetivo.set_password(form.cleaned_data["clave"])
    objetivo.save(update_fields=["password"])

    # Queda en bitácora porque restablecer la contraseña de otra persona es el
    # acto con más alcance de esta pantalla: permite entrar como ella.
    logger.warning(
        "Contraseña restablecida | usuario=%s por=%s",
        objetivo.get_username(),
        request.user.get_username(),
    )
    messages.success(
        request,
        f"Contraseña de «{objetivo.get_username()}» restablecida. Entréguesela "
        "en persona y pídale que la cambie.",
    )
    return redirect("web:operador", pk=objetivo.pk)


def _cambiar_estado_operador(request, objetivo):
    activar = request.POST.get("activo") == "1"

    if not activar:
        if objetivo.pk == request.user.pk:
            raise ErrorDeGobierno(
                "No puede desactivarse a sí mismo: perdería el acceso en el acto."
            )
        # Aquí NO se comprueba si es el último administrativo, y conviene
        # explicar por qué no hace falta: quien ejecuta esta acción es
        # forzosamente un administrativo activo —lo exige el decorador— y es
        # distinto del objetivo, porque el caso de uno mismo ya se rechazó
        # arriba. Luego siempre queda al menos un administrativo en pie. Una
        # comprobación adicional nunca se cumpliría, y el código muerto que
        # aparenta proteger algo es peor que su ausencia: induce a confiar.
        turno = services.obtener_turno_abierto(objetivo)
        if turno is not None:
            raise ErrorDeGobierno(
                f"«{objetivo.get_username()}» tiene el turno {turno.pk} abierto. "
                "Debe cerrarse primero: desactivarlo dejaría ese efectivo sin "
                "corte y sin responsable."
            )

    objetivo.is_active = activar
    objetivo.save(update_fields=["is_active"])

    logger.info(
        "Operador %s | usuario=%s por=%s",
        "activado" if activar else "desactivado",
        objetivo.get_username(),
        request.user.get_username(),
    )
    messages.success(
        request,
        f"«{objetivo.get_username()}» "
        + ("puede volver a entrar." if activar else "ya no puede entrar."),
    )
    return redirect("web:operador", pk=objetivo.pk)


def _tarifa_vigente_o_nada() -> Tarifa | None:
    """
    El precio vigente, o `None` si la caseta todavía no tiene ninguno.

    La ausencia de precio no es un error que deba interrumpir esta pantalla:
    es precisamente el estado que hay que mostrar y resolver aquí.
    """
    try:
        return services.obtener_tarifa_vigente()
    except ErrorDominio:
        return None


def _parametros_de(tarifa: Tarifa) -> ParametrosTarifa:
    return ParametrosTarifa(
        cuota_hora=tarifa.cuota_hora,
        minutos_cortesia=tarifa.minutos_cortesia,
        fraccion_minutos=tarifa.fraccion_minutos,
        cobro_minimo=tarifa.cobro_minimo,
        tope_diario=tarifa.tope_diario,
    )


def _ejemplos(parametros: ParametrosTarifa | None) -> list[dict]:
    """
    Lo que pagaría un vehículo según el tiempo que se quede.

    El cálculo lo hace el motor de tarifas, el mismo que cobra: una tabla de
    ejemplos obtenida de otra forma podría mostrar un importe que la caja no
    cobra, y entonces no serviría para comprobar nada.
    """
    if parametros is None:
        return []
    filas = []
    for minutos, etiqueta in EJEMPLOS_PERMANENCIA:
        cotizacion = cotizar(minutos, parametros)
        filas.append(
            {
                "etiqueta": etiqueta,
                "importe": cotizacion.importe,
                "es_cortesia": cotizacion.es_cortesia,
            }
        )
    return filas


def _valores_vigentes(vigente: Tarifa | None) -> dict:
    """
    Valores con que llega el formulario de cambio: los que están en uso.

    Es la mitad del arreglo. Lo que no se toca, no cambia.
    """
    if vigente is None:
        return dict(PRECIO_DE_ARRANQUE)
    return {
        "cuota_hora": vigente.cuota_hora,
        "minutos_cortesia": vigente.minutos_cortesia,
        "fraccion_minutos": vigente.fraccion_minutos,
        "cobro_minimo": vigente.cobro_minimo,
        "tope_diario": vigente.tope_diario,
    }


def _autorizar_cambio(request) -> None:
    request.session[CLAVE_DESBLOQUEO] = timezone.now().isoformat()


def _olvidar_autorizacion(request) -> None:
    request.session.pop(CLAVE_DESBLOQUEO, None)


def _cambio_autorizado(request) -> bool:
    """
    ¿Se confirmó la contraseña hace poco?

    El desbloqueo se guarda en la sesión del servidor y no en el propio
    formulario: así no viaja al navegador nada que permita reabrirlo, y caduca
    por sí solo.
    """
    marca = request.session.get(CLAVE_DESBLOQUEO)
    if not marca:
        return False
    try:
        desbloqueado_en = datetime.fromisoformat(marca)
    except (TypeError, ValueError):
        return False
    return timezone.now() - desbloqueado_en <= timedelta(minutes=MINUTOS_DESBLOQUEO)


@login_required
@require_http_methods(["GET"])
def tarifa(request):
    """
    El precio vigente, en solo lectura.

    La consulta está abierta a cualquier operador —necesita saber qué cobra—, y
    aquí no hay ningún campo que se pueda teclear por descuido: el cambio vive
    en `tarifa_cambiar`, detrás de la contraseña.
    """
    vigente = _tarifa_vigente_o_nada()

    contexto = _contexto_base(request)
    contexto["seccion"] = "tarifa"
    contexto["vigente"] = vigente
    contexto["ejemplos"] = _ejemplos(_parametros_de(vigente) if vigente else None)
    contexto["tarifas"] = Tarifa.objects.order_by("-vigente_desde")[:50]
    return render(request, "estacionamiento/tarifa.html", contexto)


@login_required
@require_http_methods(["GET", "POST"])
def tarifa_cambiar(request):
    """
    Cambio del precio: confirmar la contraseña y, recién entonces, el formulario.

    El alta exige perfil administrativo —el precio es el parámetro más sensible
    del sistema y modificarlo desde una caja sería la vía más simple de cobrar
    de menos y quedarse con la diferencia— y, además, confirmar la contraseña
    del propio operador. La contraseña no es un trámite: es lo que convierte un
    clic de más en una decisión, y deja el cambio atribuido a quien lo hizo.

    Se conserva el comportamiento de fondo: guardar crea un precio nuevo y
    desactiva el anterior. Es lo que permite demostrar qué precio estaba
    exhibido en una fecha determinada, y los folios ya cobrados mantienen el
    suyo.
    """
    if not _es_administrativo(request.user):
        messages.error(
            request,
            "Solo el perfil administrativo puede cambiar el precio. Desde una "
            "caja se consulta, no se modifica.",
        )
        return redirect("web:tarifa")

    vigente = _tarifa_vigente_o_nada()

    contexto = _contexto_base(request)
    contexto["seccion"] = "tarifa"
    contexto["vigente"] = vigente
    contexto["ejemplos"] = _ejemplos(_parametros_de(vigente) if vigente else None)

    # Primera configuración: no hay precio que proteger todavía, y el operador
    # acaba de teclear su contraseña para entrar. Pedirla otra vez sería un
    # obstáculo sin control que lo respalde.
    if vigente is None:
        _autorizar_cambio(request)

    if request.method == "POST" and request.POST.get("accion") == "desbloquear":
        form_clave = DesbloqueoPrecioForm(request.POST)
        if form_clave.is_valid() and authenticate(
            username=request.user.get_username(),
            password=form_clave.cleaned_data["clave"],
        ):
            _autorizar_cambio(request)
        else:
            messages.error(request, "Contraseña incorrecta. El precio sigue bloqueado.")

    if not _cambio_autorizado(request):
        contexto["form_clave"] = DesbloqueoPrecioForm()
        return render(request, "estacionamiento/tarifa_cambiar.html", contexto)

    if request.method == "POST" and request.POST.get("accion") == "guardar":
        form = TarifaForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            try:
                # Validación del dominio antes de persistir: una fracción
                # inválida produciría cobros erróneos de forma silenciosa.
                parametros = ParametrosTarifa(
                    cuota_hora=datos["cuota_hora"],
                    minutos_cortesia=datos["minutos_cortesia"],
                    fraccion_minutos=datos["fraccion_minutos"],
                    cobro_minimo=datos["cobro_minimo"],
                    tope_diario=datos.get("tope_diario"),
                )
            except ErrorDominio as exc:
                messages.error(request, str(exc))
            else:
                nueva = _poner_precio_en_vigencia(parametros)
                # El desbloqueo no sobrevive al cambio: el precio vuelve a
                # quedar bloqueado, como dice la pantalla.
                _olvidar_autorizacion(request)
                messages.success(
                    request,
                    f"El precio quedó en ${nueva.cuota_hora:,.2f} por hora y ya "
                    "está en vigencia. El anterior se conserva en el histórico: "
                    "los folios ya cobrados mantienen el precio con que se "
                    "calcularon.",
                )
                return redirect("web:tarifa")
        else:
            messages.error(request, "Revise los datos del precio.")
        contexto["form"] = form
    else:
        contexto["form"] = TarifaForm(initial=_valores_vigentes(vigente))

    return render(request, "estacionamiento/tarifa_cambiar.html", contexto)


def _poner_precio_en_vigencia(parametros: ParametrosTarifa) -> Tarifa:
    """
    Crea el precio nuevo y desactiva el anterior. Nunca edita en sitio.

    El nombre lleva marca de tiempo porque el modelo lo exige único y ya no se
    pide al usuario, que no tiene por qué bautizar un precio.
    """
    ahora = timezone.now()
    Tarifa.objects.filter(activa=True).update(activa=False)
    return Tarifa.objects.create(
        nombre=f"Precio {ahora:%Y-%m-%d %H:%M:%S}",
        cuota_hora=parametros.cuota_hora,
        minutos_cortesia=parametros.minutos_cortesia,
        fraccion_minutos=parametros.fraccion_minutos,
        cobro_minimo=parametros.cobro_minimo,
        tope_diario=parametros.tope_diario,
        activa=True,
        vigente_desde=ahora,
    )

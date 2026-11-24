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
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_safe

from .. import (
    graficos,
    marca,
    reglas,
    reportes_pdf,
    services,
    services_caja,
    services_mantenimiento,
    services_metricas,
    services_pensiones,
)
from ..domain.exceptions import ErrorDominio, VehiculoYaDentro
from ..domain.tarifas import ParametrosTarifa, cotizar
from ..models import EstadoFolio, EstadoTurno, Folio, Tarifa, TipoVehiculo, Turno
from .forms import (
    AbrirTurnoForm,
    AjusteCorteForm,
    CancelarFolioForm,
    CapturaForm,
    CerrarTurnoDeOtroForm,
    CerrarTurnoForm,
    ClaveOperadorForm,
    CobroForm,
    ConfirmarClaveForm,
    CorregirCambioForm,
    MetricasForm,
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
    turno_actual = services.obtener_turno_abierto(request.user)
    return {
        "turno_actual": turno_actual,
        # Cuánto lleva abierto. La caja avisaba solo cuando NO había turno
        # —lo que estorba— y callaba el turno de catorce horas, que es lo que
        # arruina el corte sin que nadie lo note.
        "estado_turno": (
            services_caja.estado_del_turno(turno_actual) if turno_actual else None
        ),
        "es_administrativo": _es_administrativo(request.user),
        # Secciones que en el teléfono viven dentro de «Más»: la pestaña se
        # marca activa en cualquiera de ellas, para que el operador sepa
        # dónde está y cómo volver.
        "secciones_de_mas": SECCIONES_DE_MAS,
    }


SECCIONES_DE_MAS = ("mas", "tarifa", "reportes", "metricas", "operadores", "mantenimiento", "clave")


@login_required
@require_safe
def mas(request):
    """
    Las secciones que no caben en la barra inferior del teléfono.

    Es una página y no un menú desplegable: funciona sin JavaScript, se puede
    abrir en otra pestaña y el botón «atrás» del teléfono hace lo esperado.
    Lo que muestra depende del perfil, igual que la cabecera de escritorio:
    un operador no ve destinos que le negarían el acceso.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "mas"
    return render(request, "estacionamiento/mas.html", contexto)


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
    extra_context = {"seccion": "clave"}

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


@require_safe
def manifiesto(request):
    """
    Manifiesto de la aplicación instalable, generado y no servido como archivo.

    Dos motivos, los dos comprobados en producción:

    El nombre. Era la última copia del nombre comercial escrita a mano; aquí
    sale de `marca.nombre_comercial()`, como el resto.

    Los iconos. Un archivo estático solo puede nombrarlos por su ruta fija, y
    esa ruta no cambia cuando cambia el dibujo: el teléfono que ya la tenía en
    caché sigue mostrando el icono viejo. `staticfiles_storage.url` devuelve
    la ruta con la huella del contenido, de modo que un icono nuevo es una
    dirección nueva.

    No exige sesión: el navegador lo pide antes de que nadie entre, y un
    manifiesto solo describe cómo mostrar la aplicación.
    """
    nombre = marca.nombre_comercial()

    def icono(nombre_archivo: str, lado: int, proposito: str) -> dict:
        return {
            "src": staticfiles_storage.url(f"estacionamiento/{nombre_archivo}"),
            "sizes": f"{lado}x{lado}",
            "type": "image/png",
            "purpose": proposito,
        }

    return JsonResponse(
        {
            # `id` fija la identidad de la aplicación instalada: sin él, el
            # navegador la deduce de `start_url` y un cambio de ruta crearía
            # un acceso directo distinto en lugar de actualizar el existente.
            "id": "/",
            "name": nombre,
            "short_name": nombre,
            "description": "Caja de estacionamiento: entradas, cobros y corte de turno.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            # El grafito de la marca: la ventana de la aplicación instalada
            # arranca del mismo color que su cabecera y su pantalla de acceso.
            "background_color": "#1f2128",
            "theme_color": "#1f2128",
            "lang": "es-MX",
            "icons": [
                icono("icono-192.png", 192, "any"),
                icono("icono-512.png", 512, "any"),
                # Mascarables: el lanzador de Android recorta con la forma del
                # teléfono. Sin una versión declarada así, mete el icono en un
                # círculo blanco y lo encoge, y la pantalla de inicio muestra
                # el logotipo pequeño sobre una placa que no es de la marca.
                icono("icono-mascarable-192.png", 192, "maskable"),
                icono("icono-mascarable-512.png", 512, "maskable"),
            ],
        }
    )


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

    Desde el rediseño hay **una sola captura de placa** con dos botones. El
    servidor decide qué hacer según el estado de esa placa, y los casos que
    antes eran un error —entrada de una placa que ya está dentro, cobro de una
    que no— se resuelven ofreciendo lo que el operador seguramente quería.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "caja"
    contexto.update(
        {
            "form_captura": CapturaForm(),
            "form_cobro": None,
            "cotizacion": None,
            "folio_cotizado": None,
            # Resultado del último cobro, de lectura única: se muestra una vez
            # tras la redirección y desaparece al recargar.
            "resultado": request.session.pop(CLAVE_RESULTADO_COBRO, None),
        }
    )

    if request.method == "POST":
        accion = request.POST.get("accion")
        try:
            if accion == "entrada":
                return _procesar_entrada(request, contexto)
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

    return _pintar_caja(request, contexto)


# Clave de sesión del resultado del cobro. El cambio a entregar no viaja en la
# dirección: una URL con importes queda en el historial del navegador.
CLAVE_RESULTADO_COBRO = "caja_resultado_cobro"

# Billetes que se ofrecen como atajo de pago. Configurables porque dependen de
# lo que circula en la zona y de la cuota; por omisión, los de uso común.
ATAJOS_DE_PAGO_POR_OMISION = (50, 100, 200, 500)


def _pintar_caja(request, contexto, status: int = 200):
    """Completa lo que toda versión de la caja muestra y la dibuja."""
    contexto.setdefault("patio", services.folios_en_patio())
    contexto.setdefault("ocupacion", services.consultar_ocupacion())
    return render(request, "estacionamiento/caja.html", contexto, status=status)


def _atajos_para(importe: Decimal) -> list[int]:
    """
    Billetes con los que tiene sentido pagar este importe.

    Solo los que alcanzan: un atajo de $50 ante un cobro de $80 es un botón que
    siempre termina en «pago insuficiente».
    """
    billetes = getattr(settings, "IRCARS_ATAJOS_DE_PAGO", ATAJOS_DE_PAGO_POR_OMISION)
    return [b for b in billetes if Decimal(b) >= importe]


def _preparar_cobro(contexto, folio, cotizacion) -> None:
    """
    Pone en el contexto la tarjeta de cobro de un folio ya cotizado.

    De un pensionado se prepara además su situación: si la estancia quedó
    cubierta, la tarjeta es de salida sin cobro; si se pasó, el importe es el
    del excedente y la tarjeta ofrece renovar, que lo cubriría.
    """
    if folio.pension_id:
        salida = services.cotizar_salida_pension(folio)
        contexto["salida_pension"] = salida
        contexto["vigencia_pension"] = services_pensiones.vigencia(folio.pension)
    form = CobroForm(initial={"folio": folio.uuid})
    # El importe viaja al campo para el cambio estimado del navegador. Es solo
    # una ayuda visual: el cobro lo recalcula el servidor.
    form.fields["pago_recibido"].widget.attrs["data-importe"] = str(cotizacion.importe)
    contexto.update(
        {
            "folio_cotizado": folio,
            "cotizacion": cotizacion,
            "form_cobro": form,
            "atajos": _atajos_para(cotizacion.importe),
        }
    )


def _procesar_entrada(request, contexto):
    form = CapturaForm(request.POST)
    if not form.is_valid():
        # Se vuelve a dibujar en lugar de redirigir: el error aparece junto al
        # campo y lo tecleado se conserva.
        contexto["form_captura"] = form
        return _pintar_caja(request, contexto, status=400)

    placa = form.cleaned_data["placa"]
    try:
        folio = services.registrar_entrada(
            placa=placa,
            tipo_cajon=form.cleaned_data["tipo_cajon"],
            operador=request.user,
        )
    except VehiculoYaDentro:
        # No es un error de quien opera: casi siempre es el vehículo que sale
        # y se tecleó en «Entrada». Se le ofrece directamente su cobro.
        existente = services.buscar_folio_abierto_por_placa(placa)
        if existente is None:
            raise
        messages.info(
            request,
            f"{existente.placa} ya estaba dentro desde las "
            f"{timezone.localtime(existente.entrada_en):%H:%M}. "
            "Si está saliendo, cobre aquí.",
        )
        _preparar_cobro(contexto, existente, services.cotizar_folio(existente))
        return _pintar_caja(request, contexto)

    ubicacion = f" · cajón {folio.cajon.codigo}" if folio.cajon else ""
    if folio.pension_id:
        # El pensionado no recibe boleto: su estancia no se cobra por hora y
        # no hay importe que consultar. Vuelve a la caja, lista para el
        # siguiente vehículo.
        hasta = timezone.localtime(folio.pension.vigente_hasta)
        messages.success(
            request,
            f"Entrada de pensionado {folio.placa}{ubicacion} · cubierta hasta "
            f"el {hasta:%d/%m a las %H:%M}.",
        )
        return redirect("web:caja")

    vencida = services_pensiones.pension_activa_de(folio.placa)
    if vencida is not None:
        # Tiene pensión, pero no cubre este momento: entra por hora, y el
        # operador debe poder decírselo y ofrecerle renovar.
        cuando = (
            f"venció el {timezone.localtime(vencida.vigente_hasta):%d/%m a las %H:%M}"
            if vencida.vigente_hasta
            else "nunca se ha cobrado"
        )
        messages.warning(
            request,
            f"La pensión de {folio.placa} {cuando}: esta estancia se cobra por "
            "hora, salvo que renueve. Renovar ahora la cubre desde la entrada.",
        )
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
    from django.templatetags.static import static

    from apps.boletos.qr import QRNoDisponible, svg_qr

    try:
        # El logotipo al centro: el cliente reconoce de quién es el código
        # antes de escanearlo. Es el mismo archivo que ya muestra esta
        # pantalla arriba, de modo que el navegador no lo pide dos veces.
        return svg_qr(url, escala=6, logo_url=static("estacionamiento/icono-192.png"))
    except QRNoDisponible:
        logger.warning("No se pudo generar el código QR del boleto.")
        return None


def _procesar_cotizacion(request, contexto):
    form = CapturaForm(request.POST)
    if not form.is_valid():
        contexto["form_captura"] = form
        return _pintar_caja(request, contexto, status=400)

    placa = form.cleaned_data["placa"]
    folio = services.buscar_folio_abierto_por_placa(placa)
    if folio is None:
        # En lugar de un «no existe» seco, las placas del patio que contienen
        # lo tecleado —el operador suele recordar solo una parte— y la opción
        # de registrar la entrada, por si se pulsó el botón equivocado.
        buscado = services.normalizar_placa(placa)
        patio = services.folios_en_patio()
        # Sin guiones de ninguno de los dos lados: «ABC12» debe sugerir
        # «ABC-1234», que es como el operador lo recuerda a medias.
        sin_guion = buscado.replace("-", "")
        contexto.update(
            {
                "patio": patio,
                "sugerencias": [
                    f for f in patio if sin_guion in f["placa"].replace("-", "")
                ][:5],
                "placa_no_encontrada": buscado,
                "form_captura": form,
            }
        )
        return _pintar_caja(request, contexto)

    # La cotización se renderiza directamente, sin redirección: es una
    # operación de solo lectura y el resultado debe quedar en pantalla junto al
    # formulario de cobro.
    _preparar_cobro(contexto, folio, services.cotizar_folio(folio))
    return _pintar_caja(request, contexto)


def _salida_cubierta(uuid_folio) -> bool:
    """
    Si este folio es de un pensionado que sale dentro de su vigencia.

    Esa salida no mueve efectivo, así que no le aplica el freno del turno de
    otra jornada ni exige turno: la caseta no debe retener a quien ya pagó su
    día porque alguien olvidó cerrar la caja.
    """
    folio = Folio.objects.filter(
        uuid=uuid_folio, estado=EstadoFolio.ABIERTO, pension__isnull=False
    ).first()
    return folio is not None and services.cotizar_salida_pension(folio).importe == 0


def _procesar_cobro(request):
    form = CobroForm(request.POST)
    if form.is_valid() and _salida_cubierta(form.cleaned_data["folio"]):
        folio, _ = services.registrar_salida(
            uuid_folio=form.cleaned_data["folio"], operador=request.user
        )
        request.session[CLAVE_RESULTADO_COBRO] = {
            "placa": folio.placa,
            "importe": "0.00",
            "pago_recibido": None,
            "cambio": None,
            "es_cortesia": False,
            "es_pension": True,
        }
        return redirect("web:caja")

    turno = services.obtener_turno_abierto(request.user)
    if turno is not None and services_caja.exige_cierre_antes_de_cobrar(turno):
        # Se frena el cobro, no la entrada: un vehículo en la rampa no puede
        # esperar a que alguien cuadre la caja, pero un cobro que cae en el
        # turno de ayer mezcla el efectivo de dos días y deja la diferencia
        # del corte sin lectura posible.
        messages.error(
            request,
            f"El turno {turno.pk} sigue abierto desde el "
            f"{timezone.localtime(turno.abierto_en):%d/%m a las %H:%M}. "
            "Ciérrelo antes de cobrar: si no, el efectivo de dos días se "
            "mezcla en un mismo corte. Las entradas puede seguir "
            "registrándolas.",
        )
        return redirect("web:turno")

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

    # El resultado se muestra como tarjeta propia y no como un aviso más: el
    # cambio es la cifra que se dice en voz alta, y en un aviso tenía el tamaño
    # de cualquier mensaje. Viaja por la sesión, de lectura única, y la
    # redirección tras POST se conserva: recargar no cobra dos veces.
    request.session[CLAVE_RESULTADO_COBRO] = {
        "placa": folio.placa,
        "importe": str(folio.importe),
        "pago_recibido": str(folio.pago_recibido) if folio.pago_recibido is not None else None,
        "cambio": str(folio.cambio) if folio.cambio is not None else None,
        "es_cortesia": cotizacion.es_cortesia,
    }
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
    messages.warning(request, f"Boleto de {folio.placa} cancelado.")
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

    El cierre va en dos pasos: `cerrar` muestra la cifra capturada para
    confirmarla —sin el esperado ni la diferencia— y `confirmar` corta el
    turno; `corregir` vuelve al formulario con lo capturado.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "turno"
    turno_actual = contexto["turno_actual"]
    # El formulario de cierre se conserva entre pasos: con sus errores si la
    # cifra no es válida, o con lo capturado si el cajero vuelve de la
    # confirmación a corregirla. Sin esto, volver dejaba el campo en blanco.
    form_cerrar = None

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
                # Primer paso: NO cierra. Muestra la cifra para confirmarla.
                #
                # El cierre es definitivo y un error de dedo —9825 por
                # 982.50— produce un faltante falso que solo se arregla con
                # una corrección administrativa, que es justo el caso que
                # originó `AjusteCorte`. Un paso más en la operación diaria
                # cuesta menos que esa corrección.
                if turno_actual is None:
                    messages.error(request, "No tiene un turno abierto que cortar.")
                    return redirect("web:turno")
                form = CerrarTurnoForm(request.POST)
                if form.is_valid():
                    return render(
                        request,
                        "estacionamiento/turno_confirmar.html",
                        {
                            **contexto,
                            "efectivo": form.cleaned_data["efectivo_declarado"],
                            "observaciones": form.cleaned_data.get("observaciones", ""),
                        },
                    )
                form_cerrar = form
                messages.error(request, "Capture el efectivo contado.")

            elif request.POST.get("accion") == "corregir":
                # Vuelve al formulario con lo capturado, para editar la cifra
                # en lugar de teclearla de nuevo.
                form_cerrar = CerrarTurnoForm(request.POST)

            elif request.POST.get("accion") == "confirmar":
                return _confirmar_cierre(request, turno_actual)
        except ErrorDominio as exc:
            messages.error(request, str(exc))

    if turno_actual is not None:
        contexto["avance"] = services_caja.resumen_operativo(turno_actual)
        contexto["form_cerrar"] = form_cerrar or CerrarTurnoForm()
        # El esperado antes de contar solo lo ve el perfil administrativo. El
        # cierre sigue siendo ciego para el cajero, que es donde el control
        # tiene sentido: quien cuenta no debe conocer la cifra a la que debe
        # llegar. Al dueño ocultársela no controla a nadie —puede consultarla
        # en Reportes— y le impide detectar un desvío mientras aún hay tiempo.
        if contexto["es_administrativo"]:
            contexto["esperado"] = services_caja.calcular_resumen(turno_actual)
    else:
        contexto["form_abrir"] = AbrirTurnoForm()

    # Las cajas que otros tienen abiertas, solo para el perfil administrativo.
    # Al cajero no se le muestran: le darían el esperado de un compañero y,
    # con él, una referencia para ajustar el suyo. El turno propio queda
    # fuera de la lista porque ya ocupa la parte superior de la pantalla.
    if contexto["es_administrativo"]:
        ajenos = services_caja.turnos_abiertos()
        if turno_actual is not None:
            ajenos = ajenos.exclude(pk=turno_actual.pk)
        contexto["turnos_de_otros"] = [
            {
                "turno": abierto,
                "avance": services_caja.resumen_operativo(abierto),
                "esperado": services_caja.calcular_resumen(abierto),
                "estado": services_caja.estado_del_turno(abierto),
            }
            for abierto in ajenos
        ]

    contexto["turnos_cerrados"] = (
        Turno.objects.select_related("operador", "cerrado_por")
        .filter(estado=EstadoTurno.CERRADO)
        .order_by("-cerrado_en")[:50]
    )
    return render(request, "estacionamiento/turno.html", contexto)


def _confirmar_cierre(request, turno_actual):
    """
    Segundo paso del cierre: corta el turno con la cifra ya confirmada.

    La cifra vuelve a validarse aquí. Viaja en un campo oculto, y el servidor
    no da por buena ninguna que no haya comprobado él mismo.
    """
    turno_enviado = request.POST.get("turno", "")

    if turno_actual is None:
        # Un doble toque en «Confirmar» llega aquí con el turno ya cerrado por
        # el primero. Lo correcto es llevarlo al comprobante que acaba de
        # generar, no decirle que no tiene turno.
        ya_cerrado = Turno.objects.filter(
            pk=turno_enviado if turno_enviado.isdigit() else None,
            operador=request.user,
            estado=EstadoTurno.CERRADO,
        ).first()
        if ya_cerrado is not None:
            return redirect("web:corte", turno_id=ya_cerrado.pk)
        messages.error(request, "No tiene un turno abierto que cortar.")
        return redirect("web:turno")

    # La confirmación se refiere a un turno concreto. Si entre un paso y otro
    # el turno cambió —se cerró y se abrió otro desde otra pestaña—, la cifra
    # confirmada era la de otra caja y no se aplica.
    if turno_enviado != str(turno_actual.pk):
        messages.error(
            request,
            "El turno cambió mientras confirmaba. Cuente de nuevo y capture "
            "el efectivo de este turno.",
        )
        return redirect("web:turno")

    form = CerrarTurnoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Capture el efectivo contado.")
        return redirect("web:turno")

    try:
        cerrado, _ = services_caja.cerrar_turno(
            turno_id=turno_actual.pk,
            efectivo_declarado=form.cleaned_data["efectivo_declarado"],
            observaciones=form.cleaned_data.get("observaciones", ""),
            cerrado_por=request.user,
        )
    except ErrorDominio as exc:
        messages.error(request, str(exc))
        return redirect("web:turno")
    # El resultado se pasa por la URL del comprobante en lugar de un mensaje:
    # el corte merece una pantalla propia, con el desglose completo e
    # imprimible.
    return redirect("web:corte", turno_id=cerrado.pk)


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def cerrar_turno_de_otro(request, turno_id: int):
    """
    Cierra el turno de otro operador, cuando éste se fue sin cortar.

    Un turno olvidado bloquea: si viene de otra jornada su operador ya no
    puede cobrar en él, y su efectivo se va mezclando con el del día. Si el
    operador no está para cerrarlo, solo quedaba esperar a que volviera.

    Mismas garantías que la corrección del corte: solo el perfil
    administrativo, con el motivo por escrito y su contraseña, y el corte
    queda a su nombre (`Turno.cerrado_por`). Va en dos pasos como el cierre
    propio —capturar y confirmar—, y la contraseña se pide al confirmar, que
    es donde se decide; así nunca viaja en un campo oculto.

    No muestra lo que debería haber. Quien cierra tiene que contar el
    efectivo de esa caja, y con el esperado al lado del campo la tentación
    es teclearlo en lugar de contar.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "turno"
    turno_objetivo = (
        Turno.objects.select_related("operador", "cerrado_por").filter(pk=turno_id).first()
    )
    if turno_objetivo is None:
        messages.error(request, f"No existe el turno {turno_id}.")
        return redirect("web:turno")
    if turno_objetivo.estado != EstadoTurno.ABIERTO:
        # Un doble toque en «Confirmar» o un cierre desde otro equipo: lo útil
        # es el comprobante, no un error.
        messages.info(request, f"El turno {turno_objetivo.pk} ya está cerrado.")
        return redirect("web:corte", turno_id=turno_objetivo.pk)
    if turno_objetivo.operador_id == request.user.pk:
        # El propio turno se cierra por la vía normal, sin motivo ni contraseña.
        return redirect("web:turno")

    contexto["turno_objetivo"] = turno_objetivo
    contexto["estado"] = services_caja.estado_del_turno(turno_objetivo)
    contexto["paso"] = "capturar"
    plantilla = "estacionamiento/turno_cerrar_otro.html"

    if request.method != "POST":
        contexto["form"] = CerrarTurnoDeOtroForm()
        return render(request, plantilla, contexto)

    accion = request.POST.get("accion")
    form = CerrarTurnoDeOtroForm(request.POST)
    contexto["form"] = form

    if accion == "corregir":
        return render(request, plantilla, contexto)

    if not form.is_valid():
        messages.error(request, "Revise el efectivo y el motivo.")
        return render(request, plantilla, contexto)

    contexto["paso"] = "confirmar"
    contexto["form_clave"] = ConfirmarClaveForm()

    if accion != "confirmar":
        return render(request, plantilla, contexto)

    form_clave = ConfirmarClaveForm(request.POST)
    contexto["form_clave"] = form_clave
    if not form_clave.is_valid() or not authenticate(
        username=request.user.get_username(),
        password=form_clave.cleaned_data["clave"],
    ):
        messages.error(request, "Contraseña incorrecta. El turno sigue abierto.")
        return render(request, plantilla, contexto)

    try:
        cerrado, _ = services_caja.cerrar_turno(
            turno_id=turno_objetivo.pk,
            efectivo_declarado=form.cleaned_data["efectivo_declarado"],
            cerrado_por=request.user,
            motivo_cierre=form.cleaned_data["motivo"],
        )
    except ErrorDominio as exc:
        messages.error(request, str(exc))
        return redirect("web:turno")
    return redirect("web:corte", turno_id=cerrado.pk)


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
        Turno.objects.select_related("operador", "cerrado_por").filter(pk=turno_id).first()
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
    # El veredicto se calcula aparte del resumen porque el resumen es la
    # fotografía del cierre —inmutable— y el veredicto es lo que hay que leer
    # hoy, ya con las correcciones aplicadas. La pantalla muestra las dos
    # cosas: lo que se declaró y lo que resultó ser cierto.
    contexto["veredicto"] = services_caja.veredicto_del_corte(turno_consultado)
    contexto["ajustes"] = services_caja.ajustes_del_turno(turno_consultado)
    contexto["puede_ajustar"] = (
        contexto["es_administrativo"]
        and turno_consultado.estado == EstadoTurno.CERRADO
    )
    contexto["folios"] = (
        Folio.objects.filter(turno=turno_consultado, estado=EstadoFolio.CERRADO)
        .order_by("salida_en")[:MAXIMO_FILAS]
    )
    contexto["pagos_pension"] = services_pensiones.pagos_del_turno(turno_consultado)
    return render(request, "estacionamiento/corte.html", contexto)


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def ajustar_corte(request, turno_id: int):
    """
    Corrige el efectivo declarado de un corte ya cerrado.

    Restringida al perfil administrativo y en su propia dirección. El cajero
    no se corrige a sí mismo: el control de caja descansa en que la
    diferencia la constata alguien distinto de quien contó, y una pantalla que
    permitiera al cajero reescribir su propio faltante lo desarmaría por
    completo.

    Un administrador sí puede corregir un corte que cerró él mismo —el suyo o
    el de un operador que se fue sin cortar— (decisión del dueño,
    17/11/2026). Con una sola cuenta administrativa, prohibirlo dejaría ese
    error sin corrección posible; el rastro queda igual, porque el turno
    registra quién cerró (`cerrado_por`) y el ajuste quién corrigió.

    Pide la contraseña del propio administrador antes de aplicar, igual que el
    cambio de precio y el borrado de movimientos. No es ceremonia: convierte
    un clic de más en una decisión y deja la corrección atribuida a quien se
    autenticó, que es justo lo que un ajuste de caja necesita.

    La pantalla **sí** muestra el efectivo esperado antes de capturar, al
    contrario que el cierre. Aquí no hay nada que cegar: el conteo ciego ya
    ocurrió y su resultado está grabado. Ocultar el esperado ahora solo
    obligaría a adivinar la cifra con la que se quiere cuadrar.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "turno"

    turno_objetivo = (
        Turno.objects.select_related("operador", "cerrado_por").filter(pk=turno_id).first()
    )
    if turno_objetivo is None:
        messages.error(request, f"No existe el turno {turno_id}.")
        return redirect("web:turno")
    if turno_objetivo.estado != EstadoTurno.CERRADO:
        messages.error(
            request,
            f"El turno {turno_objetivo.pk} sigue abierto: no hay corte que "
            "corregir. Ciérrelo con la cifra correcta.",
        )
        return redirect("web:turno")

    contexto["turno_corte"] = turno_objetivo
    contexto["veredicto"] = services_caja.veredicto_del_corte(turno_objetivo)
    contexto["ajustes"] = services_caja.ajustes_del_turno(turno_objetivo)
    contexto["form_clave"] = ConfirmarClaveForm()
    contexto["form"] = AjusteCorteForm(
        # El esperado viene precargado: «cuadrar el corte» es la corrección
        # más frecuente y teclear a mano una cifra que el sistema ya conoce
        # solo añade ocasiones de equivocarse. Sigue siendo editable, porque
        # el efectivo real puede no coincidir con el esperado.
        initial={"efectivo_real": turno_objetivo.esperado_vigente}
    )

    if request.method == "POST":
        form = AjusteCorteForm(request.POST)
        form_clave = ConfirmarClaveForm(request.POST)
        contexto["form"] = form
        contexto["form_clave"] = form_clave

        if not form.is_valid():
            messages.error(request, "Revise los datos de la corrección.")
            return render(request, "estacionamiento/ajuste.html", contexto)

        if not form_clave.is_valid() or not authenticate(
            username=request.user.get_username(),
            password=form_clave.cleaned_data["clave"],
        ):
            messages.error(request, "Contraseña incorrecta. No se ajustó nada.")
            return render(request, "estacionamiento/ajuste.html", contexto)

        try:
            _, _, resultado = services_caja.ajustar_corte(
                turno_id=turno_objetivo.pk,
                efectivo_corregido=form.cleaned_data["efectivo_real"],
                motivo=form.cleaned_data["motivo"],
                justificacion=form.cleaned_data["justificacion"],
                autorizo=request.user,
            )
        except ErrorDominio as exc:
            messages.error(request, str(exc))
            return render(request, "estacionamiento/ajuste.html", contexto)

        if resultado.cuadra:
            aviso = (
                f"Corte {turno_objetivo.pk} corregido: la caja queda cuadrada. "
                f"El ajuste de {resultado.monto:+,.2f} quedó registrado a su nombre."
            )
        else:
            aviso = (
                f"Corte {turno_objetivo.pk} corregido en {resultado.monto:+,.2f}. "
                f"La diferencia pasa de {resultado.diferencia_anterior:+,.2f} a "
                f"{resultado.diferencia_nueva:+,.2f}."
            )
        messages.success(request, aviso)
        return redirect("web:corte", turno_id=turno_objetivo.pk)

    return render(request, "estacionamiento/ajuste.html", contexto)


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def corregir_cambio(request, turno_id: int):
    """
    Corrige el cambio con que se abrió un turno ya cerrado.

    Es la otra mitad de `ajustar_corte`. Aquélla corrige lo contado; ésta, el
    cambio de apertura, que es de donde sale «Debería haber en caja». Sin
    ella, un cambio mal declarado solo podía «cuadrarse» declarando como
    contado un efectivo que no existió, y esa cifra inflaba después el
    reporte del periodo.

    Mismas reglas que el ajuste: solo el perfil administrativo, solo sobre un
    turno cerrado y confirmando la contraseña.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "turno"

    turno_objetivo = (
        Turno.objects.select_related("operador", "cerrado_por").filter(pk=turno_id).first()
    )
    if turno_objetivo is None:
        messages.error(request, f"No existe el turno {turno_id}.")
        return redirect("web:turno")
    if turno_objetivo.estado != EstadoTurno.CERRADO:
        messages.error(
            request,
            f"El turno {turno_objetivo.pk} sigue abierto: no hay corte que corregir.",
        )
        return redirect("web:turno")

    contexto["turno_corte"] = turno_objetivo
    contexto["veredicto"] = services_caja.veredicto_del_corte(turno_objetivo)
    contexto["ajustes"] = services_caja.ajustes_del_turno(turno_objetivo)
    contexto["form_clave"] = ConfirmarClaveForm()
    # Sin precarga, al contrario que el ajuste de lo contado: ahí la cifra
    # más probable es la esperada; aquí la única que el sistema conoce es
    # justo la que está mal.
    contexto["form"] = CorregirCambioForm()

    if request.method == "POST":
        form = CorregirCambioForm(request.POST)
        form_clave = ConfirmarClaveForm(request.POST)
        contexto["form"] = form
        contexto["form_clave"] = form_clave

        if not form.is_valid():
            messages.error(request, "Revise los datos de la corrección.")
            return render(request, "estacionamiento/ajuste_cambio.html", contexto)

        if not form_clave.is_valid() or not authenticate(
            username=request.user.get_username(),
            password=form_clave.cleaned_data["clave"],
        ):
            messages.error(request, "Contraseña incorrecta. No se corrigió nada.")
            return render(request, "estacionamiento/ajuste_cambio.html", contexto)

        try:
            _, _, resultado = services_caja.corregir_fondo(
                turno_id=turno_objetivo.pk,
                fondo_corregido=form.cleaned_data["cambio_real"],
                justificacion=form.cleaned_data["justificacion"],
                autorizo=request.user,
            )
        except ErrorDominio as exc:
            messages.error(request, str(exc))
            return render(request, "estacionamiento/ajuste_cambio.html", contexto)

        aviso = (
            f"Corte {turno_objetivo.pk}: el cambio de apertura pasa de "
            f"${resultado.fondo_anterior:,.2f} a ${resultado.fondo_nuevo:,.2f} y "
            f"lo que debería haber en caja, a ${resultado.esperado_nuevo:,.2f}. "
        )
        if resultado.cuadra:
            aviso += "La caja queda cuadrada."
        else:
            aviso += (
                f"La diferencia pasa de {resultado.diferencia_anterior:+,.2f} a "
                f"{resultado.diferencia_nueva:+,.2f}."
            )
        messages.success(request, aviso)
        return redirect("web:corte", turno_id=turno_objetivo.pk)

    return render(request, "estacionamiento/ajuste_cambio.html", contexto)


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


def _metricas_solicitadas(request) -> tuple[MetricasForm, dict | None, dict | None]:
    """
    Resuelve el periodo y calcula las métricas una sola vez.

    Lo comparten la pantalla y el PDF. Es lo que garantiza que el documento
    que se manda por correo y la pantalla donde se revisó antes de mandarlo
    digan exactamente lo mismo: si cada vista armara su consulta, bastaría un
    filtro distinto para que el dueño mandara cifras que no vio.
    """
    # Los valores por omisión se mezclan con lo que venga en la dirección, en
    # lugar de dejarlos en `initial`. Un formulario ligado ignora `initial` y
    # dibuja lo que recibió: con «desde» y «hasta» en la URL, los dos campos
    # de frecuencia salían en blanco y el operador no podía saber con qué
    # umbral se calculó la lista que estaba mirando.
    enviados = {clave: valor for clave, valor in request.GET.items() if valor != ""}
    form = MetricasForm({**MetricasForm.valores_por_omision(), **enviados})
    if not form.is_valid():
        return form, None, None

    desde, hasta = _rango_local(form.cleaned_data["desde"], form.cleaned_data["hasta"])
    metricas = services_metricas.metricas_por_rango(
        desde,
        hasta,
        top_clientes=form.cleaned_data["top_clientes"],
        minimo_visitas=form.cleaned_data["minimo_visitas"],
    )
    return form, metricas, services_caja.reporte_por_rango(desde, hasta)


def _dibujos(metricas: dict) -> dict:
    """
    Convierte las métricas en los SVG de la pantalla.

    Se dibuja en el servidor y no en el navegador: la hoja de estilos de esta
    aplicación ya se niega a depender de recursos remotos —en una caseta sin
    conexión la pantalla debe verse igual— y una librería de gráficas por CDN
    rompería esa regla. Además evita mandar al navegador las cifras de caja
    en un bloque de datos aparte del que ya se muestra.
    """
    horas = metricas["horas"]
    clientes = metricas["clientes"]

    return {
        "entradas_por_hora": graficos.barras_verticales(
            [
                {
                    "etiqueta": f"{h:02d}",
                    "valor": conteo,
                    "titulo": f"{h:02d}:00 h · {conteo} entradas",
                }
                for h, conteo in enumerate(horas["entradas"])
            ],
            formato=lambda v: f"{int(v):,}",
        ),
        "ingreso_por_hora": graficos.barras_verticales(
            [
                {
                    "etiqueta": f"{h:02d}",
                    "valor": monto,
                    "titulo": f"{h:02d}:00 h · ${monto:,.2f} cobrados",
                }
                for h, monto in enumerate(horas["ingresos"])
            ],
            alto=150,
            formato=lambda v: f"${int(v):,}",
        ),
        "semana": graficos.barras_verticales(
            [
                {
                    "etiqueta": dia["dia"][:3],
                    "valor": dia["entradas"],
                    "titulo": f"{dia['dia']} · {dia['entradas']} entradas · "
                    f"${dia['ingreso']:,.2f}",
                }
                for dia in metricas["semana"]
            ],
            alto=150,
            formato=lambda v: f"{int(v):,}",
            etiquetar_todas=True,
        ),
        "serie_diaria": graficos.barras_verticales(
            [
                {
                    "etiqueta": f"{fila['dia']:%d/%m}",
                    "valor": fila["ingreso"],
                    "titulo": f"{fila['dia']:%d/%m/%Y} · ${fila['ingreso']:,.2f} "
                    f"en {fila['folios']} boletos",
                }
                for fila in metricas["serie_diaria"]
            ],
            alto=190,
            formato=lambda v: f"${int(v):,}",
        ),
        "boletos": graficos.barra_segmentada(metricas["composicion"]["boletos"]),
        "clientes": graficos.barras_horizontales(
            [
                {
                    "etiqueta": fila["placa"],
                    "valor": fila["visitas"],
                    "titulo": f"{fila['placa']} · {fila['visitas']} visitas · "
                    f"${fila['gasto']:,.2f}",
                }
                for fila in clientes["filas"][:12]
            ],
            formato=lambda v: f"{int(v)} visitas",
        ),
        "permanencia": graficos.barras_verticales(
            [
                {
                    "etiqueta": tramo["etiqueta"].replace(" min", "'").replace("Hasta ", "≤"),
                    "valor": tramo["conteo"],
                    "titulo": f"{tramo['etiqueta']} · {tramo['conteo']} estancias "
                    f"({tramo['porcentaje']} %)",
                }
                for tramo in metricas["composicion"]["permanencia"]
            ],
            alto=150,
            formato=lambda v: f"{int(v):,}",
            etiquetar_todas=True,
        ),
    }


@login_required
@solo_administrativo
@require_safe
def metricas(request):
    """
    Métricas de operación: horas pico, clientes frecuentes y composición.

    Pantalla aparte del reporte de folios. Responden a preguntas distintas
    —aquélla «qué pasó con este boleto», ésta «cómo se comporta el negocio»—
    y sus consultas pesan distinto: quien solo quiere el listado del día no
    tiene por qué esperar a los agregados del mes.

    Existe además del PDF, y no solo él, porque el dueño revisa antes de
    mandar. Las dos salidas se calculan con la misma llamada, de modo que no
    pueden discrepar.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "metricas"

    form, datos, resumen = _metricas_solicitadas(request)
    contexto["form"] = form
    if datos is None:
        return render(request, "estacionamiento/metricas.html", contexto)

    contexto["metricas"] = datos
    contexto["resumen"] = resumen
    contexto["dibujos"] = _dibujos(datos)
    contexto["parametros"] = urlencode(
        {
            "desde": form.cleaned_data["desde"].isoformat(),
            "hasta": form.cleaned_data["hasta"].isoformat(),
            "minimo_visitas": form.cleaned_data["minimo_visitas"],
            "top_clientes": form.cleaned_data["top_clientes"],
        }
    )
    return render(request, "estacionamiento/metricas.html", contexto)


@login_required
@solo_administrativo
@require_safe
def metricas_pdf(request):
    """
    El mismo reporte, como archivo PDF descargable.

    `attachment` y no `inline`: el documento existe para mandarse. Abrirlo en
    una pestaña obliga a descargarlo después desde el visor del navegador, un
    paso que en un teléfono no siempre está donde se espera.
    """
    form, datos, resumen = _metricas_solicitadas(request)
    if datos is None:
        messages.error(request, "Revise el periodo solicitado.")
        return redirect("web:metricas")

    pdf = reportes_pdf.construir_reporte(
        datos, resumen=resumen, solicitado_por=request.user.get_username()
    )
    respuesta = HttpResponse(pdf, content_type="application/pdf")
    respuesta["Content-Disposition"] = (
        f'attachment; filename="{reportes_pdf.nombre_de_archivo(datos)}"'
    )
    # Sin almacenamiento intermedio: el reporte contiene cifras de caja y no
    # debe quedar en la caché de un proxy ni del propio navegador.
    respuesta["Cache-Control"] = "no-store, private"
    return respuesta


@login_required
@solo_administrativo
@require_http_methods(["GET", "POST"])
def mantenimiento(request):
    """
    Deja la base como recién instalada: borra boletos y turnos de prueba.

    Vive en su propia dirección y no en una pantalla de uso diario. Borrar
    movimientos no es una operación de caseta, y un botón así junto a los que
    se pulsan todo el día se acaba pulsando por curiosidad.

    Tres barreras, de menor a mayor peso: solo perfil administrativo, las
    cifras de lo que se va a borrar a la vista —un número inesperado detiene
    a tiempo— y la contraseña del propio operador, que convierte un clic de
    más en una decisión y deja el borrado atribuido a quien lo hizo.

    Lo que el servicio conserva, y conviene no perder de vista: los
    operadores, que son la identidad con la que se firma cada movimiento, y
    el precio vigente, sin el cual la caseta no puede ni abrir.
    """
    contexto = _contexto_base(request)
    contexto["seccion"] = "reportes"
    contexto["resumen"] = services_mantenimiento.resumen_movimientos()
    contexto["form_clave"] = ConfirmarClaveForm()

    if request.method == "POST":
        form_clave = ConfirmarClaveForm(request.POST)
        contexto["form_clave"] = form_clave

        if not form_clave.is_valid() or not authenticate(
            username=request.user.get_username(),
            password=form_clave.cleaned_data["clave"],
        ):
            messages.error(request, "Contraseña incorrecta. No se borró nada.")
            return render(request, "estacionamiento/mantenimiento.html", contexto)

        try:
            resultado = services_mantenimiento.purgar_movimientos(
                operador=request.user,
                incluir_historico_precios=bool(request.POST.get("historico_precios")),
                # Sin respaldo local: esto corre en un servidor, cuya base no
                # se respalda desde una pantalla, y además la petición ya viene
                # dentro de una transacción, donde la copia de SQLite se
                # quedaría esperando a que se liberara la base. La pantalla lo
                # dice con todas sus letras antes de pedir la contraseña.
                respaldar=False,
            )
        except ErrorDominio as exc:
            messages.error(request, str(exc))
            return render(request, "estacionamiento/mantenimiento.html", contexto)

        aviso = (
            f"Se borraron {resultado['folios_borrados']} registro(s) de boletos "
            f"y {resultado['turnos_borrados']} turno(s)."
        )
        if resultado["precios_borrados"]:
            aviso += f" También {resultado['precios_borrados']} precio(s) del histórico."
        messages.success(request, aviso + " La caja queda lista para operar.")
        return redirect("web:mantenimiento")

    return render(request, "estacionamiento/mantenimiento.html", contexto)


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
            # Distingue la salida cubierta por pensión de una cortesía: las
            # dos valen $0 y el contador no tendría otra forma de separarlas.
            "pension",
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
                "sí" if folio.pension_id else "no",
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
    # Las dos filas siguientes rodean la tolerancia por hora. Sin ellas, todos
    # los ejemplos caen en horas exactas y el salto de precio —que es lo que
    # más reclama un cliente— quedaría invisible justo para quien fija el
    # precio. Con tolerancia en cero, las dos muestran el proporcional y no
    # estorban.
    (70, "1 h 10 min"),
    (76, "1 h 16 min"),
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
    "minutos_tolerancia": 0,
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


def _tipo_pedido(request) -> str:
    """
    Qué precio se está consultando o cambiando.

    Llega por la dirección (`?tipo=MOTO`) y se valida contra las opciones
    reales: un valor inventado seleccionaría un precio inexistente y la
    pantalla ofrecería crear uno para un tipo de vehículo que no existe.
    """
    pedido = request.GET.get("tipo") or request.POST.get("tipo") or ""
    return pedido if pedido in TipoVehiculo.values else TipoVehiculo.AUTOMOVIL


def _tarifa_vigente_o_nada(
    tipo_vehiculo: str = TipoVehiculo.AUTOMOVIL,
) -> Tarifa | None:
    """
    El precio vigente de ese tipo, o `None` si todavía no tiene ninguno.

    La ausencia de precio no es un error que deba interrumpir esta pantalla:
    es precisamente el estado que hay que mostrar y resolver aquí.
    """
    try:
        return services.obtener_tarifa_vigente(tipo_vehiculo=tipo_vehiculo)
    except ErrorDominio:
        return None


def _parametros_de(tarifa: Tarifa) -> ParametrosTarifa:
    """
    Traducción al objeto de dominio, delegada en el propio modelo.

    Copiaba los campos uno a uno, y esa copia era la tercera de cuatro en el
    repositorio: al añadir la tolerancia por hora, una tarifa correcta en base
    de datos habría cotizado aquí sin ella, y la tabla de ejemplos de esta
    misma pantalla habría mostrado precios que la caja no cobra.
    """
    return tarifa.a_parametros()


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
        "minutos_tolerancia": vigente.minutos_tolerancia,
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
    contexto = _contexto_base(request)
    contexto["seccion"] = "tarifa"

    # Un cuadro por tipo de vehículo, los dos a la vista. Esconder uno detrás
    # de una pestaña invita a cambiar el precio del automóvil y olvidar el de
    # la motocicleta, que es un desvío que nadie nota hasta que cobra mal.
    precios = []
    for tipo in TipoVehiculo:
        vigente = _tarifa_vigente_o_nada(tipo)
        parametros = _parametros_de(vigente) if vigente else None
        precios.append(
            {
                "tipo": tipo.value,
                "etiqueta": tipo.label,
                "vigente": vigente,
                "ejemplos": _ejemplos(parametros),
                # La regla dicha en palabras. La tabla de ejemplos obliga a
                # deducirla a partir de ocho importes, y así se coló en
                # producción un precio con «bloques de 15 minutos» donde iba
                # «15 minutos de tolerancia»: ninguna de las dos capturas es
                # inválida, solo cobran cosas distintas.
                "reglas": reglas.en_palabras(parametros) if parametros else [],
            }
        )

    contexto["precios"] = precios
    contexto["plan_pension"] = services_pensiones.plan_vigente_o_nada()
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

    tipo = _tipo_pedido(request)
    vigente = _tarifa_vigente_o_nada(tipo)

    contexto = _contexto_base(request)
    contexto["seccion"] = "tarifa"
    contexto["vigente"] = vigente
    # El tipo viaja en el contexto y en el formulario: la pantalla dice en
    # todo momento qué precio se está cambiando, y el envío no puede acabar
    # aplicándose al otro por haber perdido la dirección.
    contexto["tipo"] = tipo
    contexto["tipo_etiqueta"] = TipoVehiculo(tipo).label
    parametros = _parametros_de(vigente) if vigente else None
    contexto["ejemplos"] = _ejemplos(parametros)
    # La regla dicha en palabras. La tabla de ejemplos obliga a deducirla a
    # partir de ocho importes, y así se coló en producción un precio con
    # «bloques de 15 minutos» donde iba «15 minutos de tolerancia»: ninguna
    # de las dos capturas es inválida, solo cobran cosas distintas.
    contexto["reglas"] = reglas.en_palabras(parametros) if parametros else []

    # Primera configuración: no hay precio que proteger todavía, y el operador
    # acaba de teclear su contraseña para entrar. Pedirla otra vez sería un
    # obstáculo sin control que lo respalde.
    if vigente is None:
        _autorizar_cambio(request)

    if request.method == "POST" and request.POST.get("accion") == "desbloquear":
        form_clave = ConfirmarClaveForm(request.POST)
        if form_clave.is_valid() and authenticate(
            username=request.user.get_username(),
            password=form_clave.cleaned_data["clave"],
        ):
            _autorizar_cambio(request)
        else:
            messages.error(request, "Contraseña incorrecta. El precio sigue bloqueado.")

    if not _cambio_autorizado(request):
        contexto["form_clave"] = ConfirmarClaveForm()
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
                    minutos_tolerancia=datos["minutos_tolerancia"],
                    cobro_minimo=datos["cobro_minimo"],
                    tope_diario=datos.get("tope_diario"),
                )
            except ErrorDominio as exc:
                messages.error(request, str(exc))
            else:
                nueva = _poner_precio_en_vigencia(parametros, tipo_vehiculo=tipo)
                # El desbloqueo no sobrevive al cambio: el precio vuelve a
                # quedar bloqueado, como dice la pantalla.
                _olvidar_autorizacion(request)
                messages.success(
                    request,
                    f"El precio de {nueva.get_tipo_vehiculo_display().lower()} "
                    f"quedó en ${nueva.cuota_hora:,.2f} por hora y ya está en "
                    "vigencia. El anterior se conserva en el histórico: los "
                    "boletos ya cobrados mantienen el precio con que se "
                    "calcularon.",
                )
                return redirect("web:tarifa")
        else:
            messages.error(request, "Revise los datos del precio.")
        contexto["form"] = form
    else:
        contexto["form"] = TarifaForm(initial=_valores_vigentes(vigente))

    return render(request, "estacionamiento/tarifa_cambiar.html", contexto)


def _poner_precio_en_vigencia(
    parametros: ParametrosTarifa,
    tipo_vehiculo: str = TipoVehiculo.AUTOMOVIL,
) -> Tarifa:
    """
    Crea el precio nuevo y desactiva el anterior **del mismo tipo**.

    El filtro por tipo no es un detalle: sin él, cambiar el precio del
    automóvil desactivaría el de la motocicleta y la siguiente moto no podría
    entrar. La base lo impediría a medias —solo garantiza que no haya dos
    activos del mismo tipo—, así que la regla tiene que estar aquí.

    El nombre lleva marca de tiempo porque el modelo lo exige único y ya no se
    pide al usuario, que no tiene por qué bautizar un precio.
    """
    ahora = timezone.now()
    etiqueta = TipoVehiculo(tipo_vehiculo).label
    Tarifa.objects.filter(activa=True, tipo_vehiculo=tipo_vehiculo).update(
        activa=False
    )
    return Tarifa.objects.create(
        tipo_vehiculo=tipo_vehiculo,
        nombre=f"{etiqueta} {ahora:%Y-%m-%d %H:%M:%S}",
        cuota_hora=parametros.cuota_hora,
        minutos_cortesia=parametros.minutos_cortesia,
        fraccion_minutos=parametros.fraccion_minutos,
        minutos_tolerancia=parametros.minutos_tolerancia,
        cobro_minimo=parametros.cobro_minimo,
        tope_diario=parametros.tope_diario,
        activa=True,
        vigente_desde=ahora,
    )

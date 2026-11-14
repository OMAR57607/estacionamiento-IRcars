"""Rutas de la interfaz web de caja."""
from django.urls import path

from . import views, views_pensiones

app_name = "web"

urlpatterns = [
    # La caja vive en la raíz: es la pantalla que el operador abre todo el día
    # y no debe exigirle recordar una ruta. Quien llegue sin sesión acaba en
    # el formulario de acceso, que es el comportamiento esperado de un
    # sistema de caja publicado en su propia dirección.
    path("", views.caja, name="caja"),
    # Manifiesto de la aplicación instalable. Se genera en lugar de servirse
    # como archivo: así el nombre sale de la configuración y los iconos
    # llevan la huella de su contenido.
    path("manifiesto.json", views.manifiesto, name="manifiesto"),
    path("turno/", views.turno, name="turno"),
    # Secciones que no caben en la barra inferior del teléfono.
    path("mas/", views.mas, name="mas"),
    path("turno/<int:turno_id>/corte/", views.corte, name="corte"),
    # Corrección de un corte ya cerrado. En su propia dirección y no como un
    # formulario más del comprobante: llegar aquí es un acto deliberado del
    # perfil administrativo, y la vista exige confirmar la contraseña antes
    # de aplicar nada.
    path("turno/<int:turno_id>/corte/ajustar/", views.ajustar_corte, name="ajustar_corte"),
    path("reportes/", views.reportes, name="reportes"),
    # Métricas de operación: horas pico, clientes frecuentes y composición.
    # Separadas del reporte de folios porque responden a otra pregunta y
    # porque su consulta es más pesada: quien solo quiere el listado del día
    # no debe pagar por los agregados del mes.
    path("reportes/metricas/", views.metricas, name="metricas"),
    path("reportes/metricas/pdf/", views.metricas_pdf, name="metricas_pdf"),
    # Puesta en marcha: borrar los movimientos de prueba. En su propia
    # dirección, lejos de lo que se pulsa a diario.
    path("mantenimiento/", views.mantenimiento, name="mantenimiento"),
    # Alta y gobierno de operadores. Sustituye al panel de administración de
    # Django, que no se monta.
    path("operadores/", views.operadores, name="operadores"),
    path("operadores/<int:pk>/", views.operador, name="operador"),
    # Cambio de la propia contraseña. Sin panel de administración, es la única
    # vía de rotar la clave con que se creó una cuenta.
    path("clave/", views.CambiarClave.as_view(), name="clave"),
    path("tarifa/", views.tarifa, name="tarifa"),
    # El cambio de precio vive en su propia dirección y no en un formulario de
    # la pantalla anterior: llegar aquí es un acto deliberado, y la vista exige
    # confirmar la contraseña antes de mostrar nada editable.
    path("tarifa/cambiar/", views.tarifa_cambiar, name="tarifa_cambiar"),
    # Pensión diaria. El cambio de precio y las acciones que no se deshacen
    # —baja, cancelación de un pago— van por POST y en su propia dirección.
    path("pensiones/", views_pensiones.lista, name="pensiones"),
    path("pensiones/nueva/", views_pensiones.alta, name="pension_nueva"),
    path("pensiones/precio/", views_pensiones.precio, name="pension_precio"),
    path("pensiones/<int:pk>/", views_pensiones.ficha, name="pension"),
    path("pensiones/<int:pk>/cobrar/", views_pensiones.cobrar, name="pension_cobrar"),
    path("pensiones/<int:pk>/baja/", views_pensiones.baja, name="pension_baja"),
    path("pensiones/pago/<uuid:uuid>/", views_pensiones.recibo, name="pension_recibo"),
    path(
        "pensiones/pago/<int:pago_id>/cancelar/",
        views_pensiones.cancelar_pago,
        name="pension_cancelar_pago",
    ),
    # Boleto recién emitido: el QR que el cliente escanea y el enlace que se
    # le puede enviar. Va antes de la cancelación por simple orden de uso.
    path("folio/<uuid:uuid>/boleto/", views.boleto, name="boleto"),
    path("folio/<uuid:uuid>/cancelar/", views.cancelar, name="cancelar"),
]

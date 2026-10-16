"""
Pruebas de integridad de las plantillas.

Existen por un fallo real que llegó a producción: comentarios escritos con la
sintaxis `{# … #}` ocupando varias líneas. Esa forma es **de una sola línea**
en el lenguaje de plantillas de Django; al abarcar más, el motor no la
reconoce como comentario y la imprime como texto. El resultado fue una página
de boleto que mostraba al cliente los comentarios internos del código.

Lo que hace grave el fallo es que **no rompe nada**: la página responde 200,
los estilos se aplican y las pruebas que buscan textos concretos siguen
pasando. Solo se ve mirando la pantalla. De ahí estas dos comprobaciones.
"""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string

RAIZ = Path(settings.BASE_DIR) / "apps"

# `{# … #}` abarcando más de una línea: la forma que Django no reconoce.
COMENTARIO_DE_UNA_LINEA = re.compile(r"\{#.*?#\}", re.DOTALL)


def plantillas():
    return sorted(RAIZ.rglob("*.html"))


def test_hay_plantillas_que_revisar():
    """
    Guarda contra una prueba que pasa por no encontrar nada.

    Si la ruta de búsqueda cambiara, las comprobaciones de abajo recorrerían
    una lista vacía y seguirían en verde sin revisar una sola plantilla.
    """
    assert len(plantillas()) >= 5


@pytest.mark.parametrize("ruta", plantillas(), ids=lambda r: r.name)
def test_los_comentarios_multilinea_usan_la_etiqueta_comment(ruta):
    """
    `{# … #}` solo vale en una línea; para varias existe `{% comment %}`.

    Se comprueba sobre el archivo y no sobre lo renderizado porque así alcanza
    también a las plantillas que ninguna prueba llega a dibujar.
    """
    texto = ruta.read_text(encoding="utf-8")

    for encontrado in COMENTARIO_DE_UNA_LINEA.finditer(texto):
        assert "\n" not in encontrado.group(0), (
            f"{ruta.name}: comentario «{{# … #}}» de varias líneas. Django no lo "
            f"reconoce y lo imprime como texto; use "
            f"«{{% comment %}} … {{% endcomment %}}»."
        )


@pytest.mark.django_db
class TestPaginasPublicas:
    """
    Lo que ve un cliente no debe contener sintaxis de plantilla.

    Se examinan las páginas públicas porque son las que no requieren sesión:
    un residuo ahí lo ve cualquiera que escanee un código.
    """

    @pytest.mark.parametrize(
        "plantilla,contexto",
        [
            ("boletos/no_encontrado.html", {"nombre_comercial": "Pruebas"}),
        ],
    )
    def test_no_quedan_residuos_de_sintaxis(self, plantilla, contexto):
        dibujado = render_to_string(plantilla, contexto)

        assert "{#" not in dibujado, "Comentario sin interpretar en la salida"
        assert "{%" not in dibujado, "Etiqueta sin interpretar en la salida"
        assert "#}" not in dibujado
        assert "{{" not in dibujado, "Variable sin interpretar en la salida"

    def test_la_pagina_del_boleto_no_filtra_comentarios(self):
        """
        Es exactamente la página que falló.

        Mostraba al cliente, entre sus datos, los comentarios internos del
        código. Se dibuja con un contexto completo para recorrer todas sus
        ramas visibles.
        """
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from apps.boletos.presentacion import BoletoVista

        ahora = timezone.now()
        vista = BoletoVista(
            uuid_folio="0a9f6b1c-0000-4000-8000-000000000000",
            placa_enmascarada="ABC••••",
            estado="ABIERTO",
            ubicacion="A-01",
            entrada_en=ahora - timedelta(hours=2),
            salida_en=None,
            minutos=None,
            importe=None,
            cuota_hora=Decimal("30.00"),
            minutos_cortesia=15,
            fraccion_minutos=1,
            cobro_minimo=Decimal("10.00"),
            tope_diario=Decimal("200.00"),
            fin_de_ciclo=None,
        )

        dibujado = render_to_string(
            "boletos/boleto.html",
            {
                "boleto": vista,
                "nombre_comercial": "Estacionamiento de Pruebas",
                "consultado_en": ahora,
                "minutos": 120,
                "importe": Decimal("60.00"),
                "es_estimado": True,
                "cotizacion": None,
            },
        )

        assert "{#" not in dibujado
        assert "#}" not in dibujado
        assert "{%" not in dibujado
        assert "{{" not in dibujado

        # Y sigue mostrando lo que debe: no se arregló borrando contenido.
        assert "ABC••••" in dibujado
        assert "$60.00" in dibujado

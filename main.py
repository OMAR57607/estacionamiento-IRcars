"""
Punto de entrada del ejecutable local de caja.

Es el archivo que PyInstaller compila. Su única responsabilidad es arrancar
Django, abrir la ventana y garantizar que ningún fallo de inicio termine en una
ventana que se cierra sin explicación: en una caseta no hay consola donde leer
el error, por lo que el diagnóstico debe llegar en un diálogo y quedar en la
bitácora.

Admite además `--verificar`, un modo sin interfaz que comprueba el ejecutable
de punta a punta y devuelve un código de salida. Existe para que la compilación
automática pueda probar el binario de verdad, no solo confirmar que se generó.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Nombre del informe que deja el modo de verificación. Se escribe en archivo
# porque el ejecutable se compila sin consola: su salida estándar no llega a
# ninguna parte, pero el código de salida y este archivo sí son legibles.
ARCHIVO_VERIFICACION = "verificacion.txt"


def _reportar_fallo_de_arranque(error: BaseException) -> None:
    """
    Informa un fallo de inicio por el medio que esté disponible.

    Se intenta un diálogo gráfico porque el ejecutable se compila sin consola;
    si ni Tkinter puede cargarse, se cae a la salida estándar, que al menos es
    legible si alguien lo ejecuta desde una terminal.
    """
    detalle = "".join(traceback.format_exception(error))
    mensaje = (
        "No fue posible iniciar la aplicación.\n\n"
        f"{type(error).__name__}: {error}\n\n"
        "Verifique que la carpeta de datos sea accesible y con permiso de "
        "escritura."
    )
    try:
        import tkinter as tk
        from tkinter import messagebox

        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror("IRcars — Error de inicio", mensaje)
        raiz.destroy()
    except Exception:
        print(mensaje, file=sys.stderr)
        print(detalle, file=sys.stderr)


def verificar() -> int:
    """
    Autodiagnóstico del ejecutable, sin abrir la interfaz.

    Comprueba lo que de verdad puede romperse al empaquetar, que no es el
    código sino lo que PyInstaller deja fuera:

    * **Zona horaria.** El fallo más probable en Windows. El módulo `zoneinfo`
      busca la base de datos IANA en el sistema operativo, y Windows no la
      incluye; depende del paquete `tzdata` y de que PyInstaller lo haya
      empaquetado. Si falta, la aplicación no arranca y el mensaje
      (`ZoneInfoNotFoundError`) no sugiere la causa.
    * **Migraciones.** Django las descubre recorriendo paquetes en tiempo de
      ejecución, algo que el análisis estático de PyInstaller no ve. Si no se
      declaran como importaciones ocultas, el ejecutable arranca con una base
      vacía y falla en la primera operación.
    * **Motores de cálculo.** Una comprobación de que la lógica de cobro y de
      corte produce los importes esperados dentro del binario.

    Devuelve 0 si todo está correcto, 1 si algo falla.
    """
    lineas: list[str] = []
    fallos = 0

    def anotar(etiqueta: str, ok: bool, detalle: str = "") -> None:
        nonlocal fallos
        marca = "OK  " if ok else "FALLA"
        lineas.append(f"[{marca}] {etiqueta}{(' — ' + detalle) if detalle else ''}")
        if not ok:
            fallos += 1

    lineas.append("Verificación del ejecutable IRcars")
    lineas.append(f"Python {sys.version.split()[0]}")
    lineas.append(f"Empaquetado: {'sí' if getattr(sys, 'frozen', False) else 'no'}")
    lineas.append("-" * 60)

    try:
        from escritorio import arranque

        arranque.iniciar()
        anotar("Arranque de Django y migraciones", True, arranque.ruta_base_datos())
    except Exception as exc:
        anotar("Arranque de Django y migraciones", False, f"{type(exc).__name__}: {exc}")
        lineas.append(traceback.format_exc())
        _escribir_informe(lineas)
        return 1

    # --- Zona horaria ------------------------------------------------------
    try:
        from django.conf import settings
        from django.utils import timezone

        ahora = timezone.localtime()
        anotar(
            f"Zona horaria «{settings.TIME_ZONE}»",
            True,
            ahora.strftime("%d/%m/%Y %H:%M %Z"),
        )
    except Exception as exc:
        anotar(
            f"Zona horaria",
            False,
            f"{type(exc).__name__}: {exc} (¿falta el paquete tzdata en el empaquetado?)",
        )

    # --- Esquema de la base de datos --------------------------------------
    try:
        from django.db import connection

        tablas = set(connection.introspection.table_names())
        esperadas = {
            "estacionamiento_tarifa",
            "estacionamiento_cajon",
            "estacionamiento_folio",
            "estacionamiento_turno",
            "auth_user",
        }
        faltantes = esperadas - tablas
        anotar(
            "Tablas creadas por las migraciones",
            not faltantes,
            f"{len(tablas)} tablas" if not faltantes else f"faltan: {sorted(faltantes)}",
        )
    except Exception as exc:
        anotar("Tablas creadas por las migraciones", False, f"{type(exc).__name__}: {exc}")

    # --- Motor de tarificación --------------------------------------------
    try:
        from decimal import Decimal

        from apps.estacionamiento.domain.tarifas import ParametrosTarifa, cotizar

        tarifa = ParametrosTarifa(cuota_hora=Decimal("30.00"), minutos_cortesia=15)
        resultado = cotizar(90, tarifa)
        correcto = resultado.importe == Decimal("45.00")
        anotar(
            "Motor de tarifas (90 min a $30/h = $45.00)",
            correcto,
            f"calculó ${resultado.importe}",
        )
    except Exception as exc:
        anotar("Motor de tarifas", False, f"{type(exc).__name__}: {exc}")

    # --- Motor de corte de caja -------------------------------------------
    try:
        from decimal import Decimal

        from apps.estacionamiento.domain.corte import calcular_corte

        corte = calcular_corte(
            fondo_inicial=Decimal("500.00"),
            importes_cobrados=[Decimal("30.00"), Decimal("60.00")],
            efectivo_declarado=Decimal("580.00"),
        )
        correcto = corte.diferencia == Decimal("-10.00") and corte.hay_faltante
        anotar(
            "Motor de corte (faltante de $10.00)",
            correcto,
            f"calculó {corte.diferencia}",
        )
    except Exception as exc:
        anotar("Motor de corte", False, f"{type(exc).__name__}: {exc}")

    # --- Interfaz gráfica --------------------------------------------------
    # Solo se comprueba que Tkinter esté empaquetado; no se abre ventana,
    # porque el entorno de compilación no tiene pantalla.
    try:
        import tkinter  # noqa: F401
        from tkinter import ttk  # noqa: F401

        anotar("Tkinter empaquetado", True)
    except Exception as exc:
        anotar("Tkinter empaquetado", False, f"{type(exc).__name__}: {exc}")

    lineas.append("-" * 60)
    lineas.append("RESULTADO: " + ("TODO CORRECTO" if fallos == 0 else f"{fallos} FALLA(S)"))
    _escribir_informe(lineas)
    return 0 if fallos == 0 else 1


def _escribir_informe(lineas: list[str]) -> None:
    """Deja el informe en archivo y, si la hay, en la salida estándar."""
    texto = "\n".join(lineas)
    try:
        Path(ARCHIVO_VERIFICACION).write_text(texto, encoding="utf-8")
    except OSError:
        pass
    try:
        print(texto)
    except Exception:
        # El ejecutable se compila sin consola: puede no haber salida estándar.
        pass


def main() -> int:
    if "--verificar" in sys.argv:
        try:
            return verificar()
        except BaseException as error:  # noqa: BLE001 - frontera del proceso
            _escribir_informe(
                ["Verificación abortada", "".join(traceback.format_exception(error))]
            )
            return 1

    try:
        # El arranque de Django precede a cualquier importación de la interfaz:
        # las vistas importan modelos, y un modelo importado antes de
        # `django.setup()` falla con un error difícil de interpretar.
        from escritorio import arranque

        arranque.iniciar()

        from escritorio.ventana import VentanaPrincipal

        ventana = VentanaPrincipal()
        # Si el operador canceló el acceso, la ventana ya se destruyó y no hay
        # bucle de eventos que iniciar.
        if not ventana.winfo_exists():
            return 0
        ventana.mainloop()
        return 0
    except BaseException as error:  # noqa: BLE001 - frontera del proceso
        _reportar_fallo_de_arranque(error)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""
Punto de entrada del ejecutable local de caja.

Es el archivo que PyInstaller compila. Su única responsabilidad es arrancar
Django, abrir la ventana y garantizar que ningún fallo de inicio termine en una
ventana que se cierra sin explicación: en una caseta no hay consola donde leer
el error, por lo que el diagnóstico debe llegar en un diálogo y quedar en la
bitácora.
"""
from __future__ import annotations

import sys
import traceback


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


def main() -> int:
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

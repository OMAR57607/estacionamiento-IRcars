"""
Lógica pura de la publicación de boletos.

Igual que `apps.estacionamiento.domain`, este módulo **no importa Django**. Son
reglas verificables con pruebas unitarias sin base de datos, y por eso viven
separadas del modelo y de las vistas.
"""
from __future__ import annotations

# Carácter de relleno del enmascarado. Se usa el punto medio y no el asterisco
# porque es visualmente más discreto en el boleto y no se confunde con una nota
# al pie.
RELLENO = "•"

# Caracteres visibles al inicio de la placa. Tres es deliberado: en el formato
# mexicano corresponden al grupo de letras, que el propietario reconoce de
# inmediato, mientras el grupo numérico —la parte que realmente individualiza
# al vehículo— queda oculto.
VISIBLES_POR_OMISION = 3


def normalizar_placa(placa: str | None) -> str:
    """
    Deja la placa en la misma forma canónica que usa `Folio.save`.

    Se replica aquí, en lugar de importarlo del modelo, para que este módulo
    permanezca libre de Django y comprobable de forma aislada.
    """
    return (placa or "").strip().upper().replace(" ", "")


def enmascarar_placa(placa: str | None, visibles: int = VISIBLES_POR_OMISION) -> str:
    """
    Oculta la parte individualizante de la placa conservando su longitud.

    Razón de ser: la placa es un dato personal en los términos de la Ley
    Federal de Protección de Datos Personales en Posesión de los Particulares
    —permite identificar indirectamente al titular del vehículo—. El boleto se
    sirve en una dirección pública, sin contraseña, y esa dirección puede
    terminar reenviada por mensajería o quedar en el historial de un navegador
    compartido. Publicar la placa íntegra no aporta nada a la operación y sí
    añade un dato personal a una superficie pública: se aplica el principio de
    minimización.

    Se conserva la longitud original porque es lo que permite al cliente
    reconocer su boleto de un vistazo sin revelar el dato.

        >>> enmascarar_placa("ABC1234")
        'ABC••••'
        >>> enmascarar_placa("abc 12 34")
        'ABC••••'

    **Invariante de la función: nunca se revela más de la mitad de los
    caracteres.** Sin ese techo, una placa corta —las hay de cinco caracteres
    en motocicleta— quedaría mayormente a la vista con la misma ventana de
    tres, y el enmascarado sería decorativo en lugar de protector. El techo se
    aplica siempre, de modo que acortar la placa nunca amplía la fuga.

        >>> enmascarar_placa("12ABC")
        '12•••'
        >>> enmascarar_placa("ABC")
        'A••'
    """
    normalizada = normalizar_placa(placa)
    if not normalizada:
        return ""

    # El techo de la mitad es lo que convierte el enmascarado en una garantía
    # y no en una preferencia estética: cualquiera que invoque esta función con
    # una ventana mayor sigue sin poder publicar más de media placa.
    visibles = max(0, min(visibles, len(normalizada) // 2))
    return normalizada[:visibles] + RELLENO * (len(normalizada) - visibles)

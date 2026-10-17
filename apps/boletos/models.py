"""
Esta aplicación ya no persiste nada.

Hubo aquí dos modelos: una bandeja de salida en la caja local y una copia de
solo lectura en la nube. Sostenían una arquitectura que ya no existe —caja en
el mostrador publicando hacia un servicio de boletos— y que se retiró cuando
la operación pasó entera a la nube: hoy el boleto del cliente se construye
leyendo el folio, que es la fuente de verdad y vive en la misma base.

El archivo se conserva vacío a propósito. Django espera encontrarlo en una
aplicación con migraciones, y dejar constancia de por qué no hay modelos vale
más que un archivo ausente que alguien recrea sin saber qué hubo antes.
"""

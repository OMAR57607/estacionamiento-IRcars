# Boleto del cliente por código QR

El operador registra la entrada y la pantalla muestra un código QR. El cliente
lo escanea con la cámara de su teléfono y consulta, con sus propios datos
móviles, su hora de ingreso, el tiempo transcurrido, el precio que se le
exhibió al entrar y el importe acumulado al instante. El enlace también puede
enviarse por WhatsApp.

> Este documento describió durante un tiempo una arquitectura partida: una
> caja local que publicaba copias hacia un servicio de boletos en la nube.
> Esa arquitectura se retiró. Hoy la caja **es** el servicio: el boleto se
> construye leyendo el folio, que vive en la misma base.

---

## Cómo se construye

```
Entrada registrada  →  /folio/<uuid>/boleto/   (operador: QR y enlace)
                       /t/<uuid>/              (cliente: la página del boleto)
```

La dirección pública es corta a propósito: el contenido del QR determina su
densidad, y una ruta larga produce un símbolo con más módulos, más difícil de
leer para una cámara de gama baja.

El identificador es un **UUID versión 4**, aleatorio. No es el número de folio
ni se deriva de él: una dirección enumerable permitiría recorrer los boletos
del estacionamiento cambiando un dígito.

---

## Qué protege la página

| Control | Cómo |
|---|---|
| **Solo lectura** | No existe ninguna ruta pública que escriba. Un fallo en esta página no puede alterar nada. |
| **Placa enmascarada** | `ABC••••`. La regla nunca revela más de la mitad de los caracteres, y se aplica al presentar. |
| **Sin datos de caja** | Lo que la página recibe es un objeto de presentación sin campos para el operador, el turno, el efectivo ni la placa completa: una plantilla futura no puede exponerlos aunque su autor quiera. |
| **Caducidad** | Un boleto liquidado deja de responder pasadas `IRCARS_HORAS_VIGENCIA_BOLETO` horas (24 por omisión). Mientras el vehículo está dentro, el enlace no caduca. |
| **Indistinguible** | Un identificador inexistente y uno vencido responden lo mismo: contestar distinto confirmaría cuáles existieron. |
| **Sin rastro** | Cabeceras `no-store`, `noindex` y `no-referrer`. La dirección no se indexa ni se filtra al navegar. |
| **Límite de tasa** | 60 consultas por minuto y por IP. Holgado para que el cliente refresque, suficiente para cortar un sondeo. |

El importe lo calcula **el mismo motor** que cobra la caja, con los parámetros
que se exhibieron al ingresar. La cifra del teléfono no puede diferir de la de
la caseta: es el requisito funcional central de la función.

---

## Variables

| Variable | Para qué |
|---|---|
| `IRCARS_NOMBRE_COMERCIAL` | Nombre del negocio en el boleto y en la caja. |
| `IRCARS_URL_BOLETOS` | Origen que se codifica en el QR. Si no se define, se deduce del dominio público de la plataforma. |
| `IRCARS_HORAS_VIGENCIA_BOLETO` | Horas que un boleto liquidado sigue consultándose. |
| `IRCARS_DOMINIO_CANONICO` | Dominio propio. Lo que llegue por otra dirección se redirige aquí, de modo que los QR ya emitidos sigan funcionando. |
| `IRCARS_TRAS_CLOUDFLARE` | De qué cabecera se toma la IP del cliente al aplicar límites de tasa. |

---

## Diagnóstico

| Síntoma | Causa probable |
|---|---|
| «Boleto no disponible» | El folio no existe, o está liquidado y ya pasó su vigencia. |
| El QR apunta a una dirección vieja | `IRCARS_URL_BOLETOS` quedó con el dominio anterior. Los QR ya emitidos siguen funcionando si `IRCARS_DOMINIO_CANONICO` está puesto. |
| 429 al refrescar | Se alcanzó el límite por IP. Es el comportamiento esperado ante un refresco en bucle. |

# Boleto del cliente por código QR

> **Arquitectura vigente: aplicación web única.** El operador trabaja desde su
> teléfono (o cualquier dispositivo), registra la entrada y la pantalla le
> muestra el código QR para que el cliente lo escane, o el enlace para
> enviárselo por WhatsApp. El servicio en la nube es la fuente de verdad y el
> boleto se sirve **leyendo el folio directamente**.
>
> El resto del documento describe además el despliegue con **caja local**, en
> el que una aplicación de escritorio publica copias enmascaradas a un
> servicio remoto de solo lectura. Las dos formas siguen soportadas y la vista
> pública resuelve sola cuál le aplica (ver `apps/boletos/presentacion.py`),
> pero lo que está en operación es la primera.

El cliente escanea un código al entrar y consulta desde su teléfono su hora de
ingreso, el tiempo transcurrido, el precio exhibido y el importe acumulado al
instante. No instala nada, no se registra y no necesita estar conectado a la
red del estacionamiento.

---

## La decisión de arquitectura

Se evaluaron cuatro caminos antes de escribir código:

| Opción | Cómo funciona | Por qué no se eligió |
|---|---|---|
| QR autocontenido | El QR lleva el texto del boleto; la cámara lo muestra sin red | No puede mostrar el importe acumulado en vivo |
| Servidor en la red local | El teléfono consulta al equipo de la caseta por Wi-Fi | Exige que el cliente se conecte a la red; el aislamiento de clientes del router lo rompe; sin HTTPS posible |
| Todo en la nube | La caja entera corre en un servidor | **La caseta deja de cobrar cuando se cae la red.** Es la razón documentada por la que el despliegue anterior se retiró |
| **Híbrida (la implementada)** | La caja local es autoritativa y **publica** una copia mínima a un servicio de solo lectura | — |

### Lo que la opción híbrida preserva

* **La caja cobra con o sin internet.** El folio se registra en SQLite local y
  la publicación se encola. Si no hay red, la caseta opera con normalidad y
  los boletos se publican cuando vuelva.
* **La nube no es la fuente de verdad.** Si el servicio se borra por completo,
  no se pierde un peso ni un folio ni un corte. Se repuebla publicando de
  nuevo.
* **El cliente consulta con datos móviles**, desde cualquier lugar, con
  HTTPS y certificado válido.

```
   CASETA (fuente de verdad)                    NUBE (solo lectura)
┌──────────────────────────────┐          ┌──────────────────────────┐
│  Ejecutable IRcars           │          │  Servicio de boletos     │
│                              │          │                          │
│  Folio ──┬──► SQLite         │          │  POST /api/v1/boletos/   │
│          │                   │  HTTPS   │   · token compartido     │
│          └──► Bandeja de  ───┼─────────►│   · idempotente          │
│               salida         │  (async) │          │               │
│               (reintentos)   │          │          ▼               │
│                              │          │  GET /t/<uuid>/          │
│  QR ──► https://.../t/<uuid> │          │   · público, solo lectura│
└──────────────────────────────┘          └──────────────────────────┘
                                                      ▲
                                            teléfono del cliente
```

---

## Qué se publica y qué no

El principio es **minimizar en el origen**, no en el destino: lo que nunca
viaja no puede filtrarse aunque el servicio se vea comprometido.

**Sí viaja:** el UUID del folio, la placa **enmascarada**, el estado, el cajón,
la hora de entrada, la de salida si ya se liquidó, y los parámetros de la
tarifa exhibida al ingresar.

**Nunca viaja:** la placa completa, el operador de entrada o salida, el turno,
el pago recibido, el cambio entregado, las observaciones internas, el desglose
del corte ni el identificador secuencial del folio.

### La placa va enmascarada

`ABC1234` se publica como `ABC••••`. La placa es un dato personal en los
términos de la LFPDPPP —identifica indirectamente al titular del vehículo— y
la dirección del boleto es pública y reenviable.

El enmascarado conserva la longitud, para que el cliente reconozca su boleto,
y garantiza un invariante: **nunca revela más de la mitad de los caracteres**.
Se aplica en la caja, antes de enviar, y el servicio además **rechaza** una
carga con la placa íntegra: la base de la nube no puede llegar a contenerla.

### El identificador es un UUID v4, no un consecutivo

Son 122 bits de aleatoriedad. Publicar el `id` autoincremental permitiría
recorrer todos los boletos del día a partir de uno solo.

---

## Controles de seguridad

| Control | Dónde | Qué previene |
|---|---|---|
| Ninguna ruta pública escribe | `urls.py` / `urls_ingesta.py` separados | Alteración del contenido desde la cara pública |
| Token con comparación de tiempo constante | `views.IngestaBoletosView` | Inyección de boletos falsos; fuga por temporización |
| Falla cerrado sin token configurado (503) | `views.IngestaBoletosView` | Un despliegue a medio configurar que acepte cualquier escritura |
| HTTPS obligatorio al publicar | `publicador._verificar_transporte` | Que el token viaje en claro |
| Límite por IP, con la entrada *derecha* de `X-Forwarded-For` | `limites.ip_cliente` | Sondeo y agotamiento; elusión por cabecera suplantada |
| `AnonRateThrottle` en DRF | `config/settings.py` | Rutas sin sesión quedaban **sin tope** antes de este cambio |
| `no-store`, `noindex`, `no-referrer` | `views._blindar` | Cifras vencidas en caché; boletos en buscadores; fuga de la dirección por `Referer` |
| Respuesta idéntica para inexistente y vencido | `views._no_encontrado` | Confirmar qué identificadores existieron |
| Vigencia tras la salida | `views._esta_vencido` | Que la dirección siga viva en historiales y reenvíos para siempre |
| Modo «solo boletos» | `config/urls.py` | Exponer el panel y el formulario de acceso a internet sin necesidad |

El token **jamás** se registra en bitácora, ni al diagnosticar un 401. Hay una
prueba que lo verifica.

---

## Coherencia del importe con la caja

El requisito es que la cifra del teléfono y la de la caseta sean la misma: si
el cliente ve $30.00 y el operador le cobra $35.00, la función no es inútil,
es una fuente de conflicto.

Para garantizarlo, la regla de truncamiento de minutos se promovió a la capa
de dominio pura (`domain.tarifas.minutos_transcurridos`), y tanto `services`
como el servicio de boletos la invocan. No hay aritmética duplicada.

El boleto exhibe **el precio vigente al ingresar**, no el actual. El cuadro
tarifario es inmutable —un cambio crea una tarifa nueva y desactiva la
anterior—, de modo que un aumento durante la estancia no altera el boleto ya
emitido. Es lo que exige la normativa de PROFECO sobre precio exhibido, y hay
una prueba que lo fija.

---

## El boleto en la aplicación web

El recorrido del operador es de dos pasos y ninguno exige navegar:

1. Registra la entrada en la caja.
2. La pantalla salta al **boleto**: código QR grande para que el cliente lo
   escanee con su cámara, botón de **compartir por WhatsApp**, el menú de
   compartir del propio teléfono cuando el navegador lo ofrece, y el enlace
   para copiar o dictar.

Se va al boleto automáticamente porque ese es el único instante en que el
cliente está delante. Cada vehículo de la lista del patio enlaza también a su
boleto, para el caso frecuente del cliente que perdió el enlace y pide que se
lo reenvíen.

El enlace se construye con `request.build_absolute_uri`, es decir, del propio
servidor que atiende la petición. No hay un dominio escrito en la
configuración que pueda quedarse desfasado y emitir códigos que apunten a un
despliegue anterior.

El mensaje de WhatsApp **no lleva la placa**: el enlace es lo único que el
cliente necesita, y lo que no se escribe en el mensaje no viaja por WhatsApp.

### Superficie expuesta

Con la caja publicada en internet, tres decisiones que conviene no revertir:

| Decisión | Por qué |
|---|---|
| La raíz muestra una presentación al visitante, no el formulario de acceso | Quien teclea el dominio es un cliente. Antes recibía el campo de contraseña en la dirección más previsible del sitio |
| El panel de administración **no se monta** salvo que se indique `IRCARS_RUTA_ADMIN` | La aplicación no lo necesita —precio, turnos, corte y reportes tienen pantallas propias con las salvaguardas del negocio— y `/admin/` es la ruta más sondeada de cualquier sitio Django |
| El formulario de acceso limita los **envíos** por IP (10 cada 5 min) | Django no trae esa protección. Abrir la pantalla no cuenta, de modo que recargar no deja fuera al operador |

---

## Puesta en marcha

### 1. El servicio en la nube

Un servicio web con su **base PostgreSQL dedicada** (nunca compartida: ver
`DESPLIEGUE-RAILWAY.md`). Variables:

```
DJANGO_SECRET_KEY=<propia de este entorno>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=boletos.mi-estacionamiento.mx
DJANGO_TIME_ZONE=America/Mexico_City
DATABASE_URL=<referencia al servicio PostgreSQL>

IRCARS_SOLO_BOLETOS=True
IRCARS_NOMBRE_COMERCIAL=Estacionamiento IRcars
IRCARS_TOKEN_PUBLICACION=<token generado>
IRCARS_HORAS_VIGENCIA_BOLETO=24
IRCARS_TRAS_PROXY=True
```

Genere el token con:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`IRCARS_URL_BOLETOS` se deduce de `RAILWAY_PUBLIC_DOMAIN` y no hace falta
fijarla, salvo con dominio propio.

Sondas: `/healthz` responde si el proceso vive. En modo «solo boletos» la
sonda de base de datos de la aplicación no está montada, así que vigile
`/healthz` y los registros.

### 2. La caja

Cree `%LOCALAPPDATA%\IRcars\boletos.json`:

```json
{
  "activo": true,
  "url": "https://boletos.mi-estacionamiento.mx",
  "token": "<el MISMO valor que IRCARS_TOKEN_PUBLICACION>"
}
```

Se usa un archivo y no variables de entorno porque en la caseta el programa se
abre con un doble clic y nadie va a definir variables ahí; un archivo se edita
con el Bloc de notas. Contiene una credencial: el programa restringe su
lectura al propietario en cada arranque.

Al reiniciar, la caja publica en segundo plano y muestra el QR al registrar
cada entrada.

### 3. Primera carga y verificación

Al encender el servicio, los vehículos **que ya están dentro** no tienen
boleto publicado: nunca se publicó uno para ellos. Si no hace nada, su cliente
vería «boleto no disponible» justo el primer día. Reconstruya la carga inicial:

```
# Qué se republicaría, sin tocar nada
python manage.py republicar_boletos --simular

# Reencolar los vehículos dentro y enviarlos en el acto
python manage.py republicar_boletos --publicar
```

Después, para el día a día:

```
# Qué hay pendiente y por qué
python manage.py publicar_boletos --solo-listar

# Forzar el envío de todo lo acumulado
python manage.py publicar_boletos
```

### 4. Retención (obligatoria)

Programe a diario en la nube:

```
python manage.py depurar_boletos
```

Borra los boletos liquidados o cancelados con más de siete días. Es la
aplicación del principio de temporalidad de la LFPDPPP: el historial contable
vive en la caja, no aquí.

---

## Reconstruir el servicio tras una pérdida

La bandeja de salida es una **cola de trabajo, no un historial**: al entregarse
una publicación, su fila desaparece. Es lo correcto para la operación diaria
—acota la bandeja al trabajo pendiente— pero implica que **nada reenvía por su
cuenta lo ya entregado**.

Consecuencia práctica: si la base del servicio se vacía o se recrea, los folios
abiertos no vuelven solos. Se corregirían al cobrarlos, pero mientras el auto
está dentro su dueño no podría consultar nada.

`republicar_boletos` cierra ese hueco:

```
# Solo los vehículos que siguen dentro (lo habitual)
python manage.py republicar_boletos --publicar

# Incluir también los liquidados cuya página siga vigente
python manage.py republicar_boletos --incluir-cerrados --publicar

# Ampliar la ventana, por ejemplo al migrar de proveedor
python manage.py republicar_boletos --incluir-cerrados --horas 72 --publicar
```

Es seguro repetirlo: la ingesta es idempotente y el encolado es un *upsert*.
Lo único que produce una repetición es trabajo de más.

Por omisión no toca los folios cerrados, y con `--incluir-cerrados` se limita a
los que siguen dentro de la vigencia del boleto público. Republicar uno más
antiguo sería trabajo inútil: la vista lo contestaría con un 404 por vencido en
cuanto llegara.

Cada folio se encola en su propia transacción, nunca el lote entero: una
transacción larga sobre SQLite retiene el bloqueo de escritura de toda la base,
y el comando puede ejecutarse con la caseta operando. Un cobro esperando a que
termine una republicación sería un cliente esperando en la rampa.

---

## Operación y diagnóstico

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El QR no aparece al dar entrada | `activo` en `false`, o falta `url` o `token` | Revise `boletos.json` y reinicie |
| «Boleto no disponible» al escanear | Aún no se publicó, o la bandeja falla | `publicar_boletos --solo-listar` |
| Pendientes con error HTTP 401 | Los tokens no coinciden | Iguálelos en los dos extremos |
| Pendientes con «debe usar https» | `url` con `http://` | Corrija a `https://` |
| Pendientes con error HTTP 503 | Falta `IRCARS_TOKEN_PUBLICACION` en el servicio | Defínala y redespliegue |
| La bandeja crece sin parar | Corte de red prolongado | Es normal; se vacía al volver. Lo de más de 3 días se descarta solo |
| «Boleto no disponible» en autos que ya estaban dentro | Se publicaron antes de que existiera la base actual, o nunca | `republicar_boletos --publicar` |

Las esperas entre reintentos crecen de 15 s hasta un techo de 10 minutos. Un
rechazo del servicio (4xx) espera el máximo desde el primer intento: no se
arregla reintentando, lo arregla una persona.

---

## Lo que **no** incluye este trabajo

* **La interfaz web de caja no muestra el QR.** El código está en
  `apps/boletos/qr.py` y es reutilizable, pero el enganche se hizo en el
  ejecutable de escritorio, que es lo que opera hoy. Añadirlo a
  `apps/estacionamiento/web/` es una vista más.
* **El QR no se imprime en papel térmico.** Se muestra en pantalla para que el
  cliente lo escanee. `qr.svg_qr` existe para un comprobante imprimible
  cuando haga falta.
* **No hay notificaciones.** El cliente consulta cuando quiere; el servicio no
  envía nada.

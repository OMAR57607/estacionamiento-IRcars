# Controlador de Estacionamiento — IRcars

Sistema de control de estacionamiento con cobro por hora y **fracción
proporcional**, control de turnos y **corte de caja con cierre ciego**.

## Modo de operación: en la nube

La caja es una **aplicación web** que el operador abre desde el teléfono, la
tableta o una computadora, con PostgreSQL gestionado detrás. Una sola
instalación sirve a cualquier dispositivo y no hay nada que instalar ni
actualizar en la caseta.

Hubo antes un ejecutable de escritorio con SQLite en el equipo de la caseta,
y durante un tiempo convivieron: la caja local publicaba hacia un servicio de
boletos en la nube. Esa arquitectura se retiró por completo cuando la
operación pasó entera a la web. Queda en el historial de git, no en el árbol
de trabajo, porque dos motores de cobro —aunque compartan el núcleo— son dos
sitios donde corregir el mismo error.

**Lo que esto cuesta y hay que saberlo:** sin internet, la caseta no cobra.
Es la contrapartida de operar en la nube y está anotada como el trabajo
siguiente.

---

## Boleto del cliente por código QR

El operador registra la entrada y la caja muestra un código QR. El cliente lo
escanea con la cámara de su teléfono y consulta, desde donde quiera y con sus
propios datos móviles, su hora de ingreso, el tiempo transcurrido, el precio
exhibido y el importe acumulado al instante.

El boleto se construye **leyendo el folio**, que es la fuente de verdad y vive
en la misma base:

- La dirección es un UUID v4 no enumerable (`/t/<uuid>/`), de solo lectura y
  sin sesión. No existe ninguna ruta pública que escriba.
- La placa se muestra **enmascarada** (`ABC••••`): es un dato personal y la
  dirección del boleto es pública y reenviable. Lo que la página recibe es un
  objeto de presentación que no tiene siquiera dónde llevar la placa completa,
  el operador, el turno ni el efectivo.
- El importe sale del **mismo motor** que cobra la caja, de modo que la cifra
  del teléfono no puede diferir de la de la caseta.

Detalle de los controles en [BOLETO-QR.md](BOLETO-QR.md).

---

## Corte de caja: corrección de un cierre equivocado

Un turno se cerró declarando $140 menos de los que había en caja. El corte
cerrado es inmutable por diseño —`calcular_resumen` devuelve su fotografía y no
un recálculo—, de modo que no había forma de enmendarlo. Las dos salidas que
quedaban eran malas: dejar el faltante falso envenena el histórico, porque si
la diferencia de un corte puede ser un error de dedo deja de significar algo y
el control de caja entero se vuelve decorativo; y editar la base a mano corrige
la cifra y borra al responsable.

La corrección se modela como **asiento de ajuste**, no como reescritura. Es el
criterio que la contabilidad aplica a un asiento equivocado —no se borra, se
contra-asienta— y por el mismo motivo.

- **El cierre no se toca.** `Turno.efectivo_declarado` y `Turno.diferencia`
  conservan para siempre lo que el operador firmó.
- **La corrección vive aparte**, en `AjusteCorte`: de qué cifra se partió, a
  cuál se llegó, el motivo —lista cerrada, para poder contar cuántas veces se
  repite el mismo problema—, la justificación escrita y quién la autorizó.
  Dos restricciones de base la sostienen: un ajuste no puede valer cero ni
  quedarse sin justificación.
- **El comprobante muestra las dos cifras.** Esa coexistencia es lo que hace
  del ajuste un acto auditable en lugar de un borrado: un faltante que puede
  desaparecer sin rastro no disuade a nadie de provocarlo.
- **Quien cerró mal no se corrige a sí mismo.** La pantalla está restringida al
  perfil administrativo y exige confirmar la contraseña, igual que el cambio de
  precio. El cierre ciego descansa en que la diferencia la constate alguien
  distinto de quien contó.
- **Varias correcciones se acumulan.** La segunda parte de la cifra vigente, no
  de la original, para que no se pisen.

Cada ajuste se registra en la bitácora en nivel `WARNING`, incluso cuando deja
la caja cuadrada: un patrón de correcciones que siempre cuadran es exactamente
lo que se vería si alguien usara esta función para tapar faltantes.

---

## Métricas de operación y reporte en PDF

Pantalla propia (`/reportes/metricas/`), restringida al perfil administrativo, y
el mismo contenido como archivo descargable (`/reportes/metricas/pdf/`).

| Métrica | Qué decide | Cómo se cuenta |
|---|---|---|
| **Horas pico** | A qué hora hace falta alguien en la caseta | Entradas por hora del día, en zona local, acumuladas en el periodo. Los empates se informan: dos horas iguales son dos turnos a cubrir |
| **Ingreso por hora de cobro** | Nada de personal — va aparte por eso | Recaudación por hora de salida. Desplazada respecto a las entradas tanto como dura la estancia media |
| **Clientes frecuentes** | A quién conviene ofrecer una pensión | Placas con N visitas o más dentro del periodo, con visitas, gasto, ticket, estancia media y parte del ingreso. El umbral y el tamaño de la lista son parámetros de la pantalla |
| **Día de la semana / día a día** | Horarios y previsión | Entradas e ingreso agregados en la base |
| **Composición** | Si el ingreso depende del precio o de la rotación | Cobros frente a cortesías, tipo de vehículo, operador y distribución de permanencia |
| **Control de caja** | Si hay fuga de efectivo | Diferencia **vigente** del periodo —ya con las correcciones— junto a la declarada al cerrar |

El límite que conviene no olvidar al leer el reporte: la placa identifica al
vehículo, no a la persona, y un cambio de placa interrumpe la serie de ese
cliente. Para lo que sirve —saber a quién retener— es suficiente.

**Por qué un PDF generado en el servidor.** El documento se manda. «Imprimir a
PDF» desde el navegador produce un archivo distinto en cada equipo y no se puede
generar desde un proceso programado. Los gráficos se dibujan vectoriales, no
como imagen: un reporte de treinta días pesa unos 22 KB, se amplía sin pixelarse
y sus rótulos siguen siendo texto.

**Por qué las gráficas se dibujan en el servidor.** Misma regla que ya cumple la
hoja de estilos —ni fuentes ni CSS remotos—: si se cae la conexión a un CDN, la
pantalla debe verse igual. Además evita mandar al navegador las cifras de caja
en un bloque de datos aparte del que ya se muestra.

**Color.** Una sola serie por gráfica y un solo matiz; la longitud de la barra
lleva el dato y el color no transporta información, de modo que el reporte sigue
siendo exacto impreso en blanco y negro. El máximo se marca con un segundo paso
del mismo azul —no con otro color— por el mismo motivo. La paleta está
verificada por banda de luminosidad, piso de croma, separación bajo protanopia y
tritanopia, y contraste contra la superficie, en los temas claro y oscuro. No
hay gráficas de pastel: dos longitudes sobre una recta se comparan mejor que dos
ángulos.

---

## Arquitectura

Las dependencias apuntan siempre hacia el dominio. El núcleo de negocio no
conoce Django, HTTP ni la base de datos, por lo que es verificable de forma
aislada y reutilizable por cualquier interfaz futura.

```
  web/views.py        Interfaz web de caja (plantillas Django) ─┐ dos interfaces,
  views.py            API REST (DRF)                           ─┤ cero reglas
  api_errors.py       Errores de dominio → HTTP                 ─┘ propias
  graficos.py         Gráficas en SVG, dibujadas en el servidor ─┐ dos salidas,
  reportes_pdf.py     El mismo reporte, en PDF (ReportLab)      ─┘ un cálculo
        │
        ▼
  services.py           Casos de uso: entrada, cotización, cobro, cancelación
  services_caja.py      Casos de uso: turno, cierre ciego, corte y su corrección
  services_metricas.py  Consultas agregadas del periodo
        │
        ▼
  domain/tarifas.py  Motor de tarificación    ─┐ lógica pura, sin Django,
  domain/corte.py    Corte de caja y su ajuste ─┤ sin ORM, sin reloj propio
  domain/metricas.py Hora pico, tramos, escalas ┤
  domain/exceptions.py                          ─┘
        │
        ▼
  models.py         Persistencia e integridad (PostgreSQL / SQLite)
```

| Capa | Archivos | Responsabilidad |
|---|---|---|
| Interfaz | `web/`, `views.py`, `serializers.py`, `graficos.py`, `reportes_pdf.py` | Deserializar, delegar, presentar |
| Aplicación | `services.py`, `services_caja.py`, `services_metricas.py` | Orquestar casos de uso, transacciones, concurrencia |
| Dominio | `domain/tarifas.py`, `domain/corte.py`, `domain/metricas.py` | Calcular importes, diferencias y conclusiones. Sin framework, 100 % testeable |
| Infraestructura | `models.py`, `migrations/` | Persistir y hacer cumplir la integridad |

La pantalla de métricas y el PDF salen de una sola llamada a
`metricas_por_rango`. Es deliberado: el dueño revisa en pantalla y manda el
archivo, y dos documentos que discrepan en una cifra obligan a averiguar cuál
de los dos miente.

---

## Puesta en marcha en local

### Opción A — Docker Compose (recomendada)

Levanta la aplicación y PostgreSQL 16 sin instalar nada más en su equipo.

```bash
git clone https://github.com/OMAR57607/estacionamiento-IRcars.git
cd estacionamiento-IRcars

cp .env.example .env
# Genere una clave propia y colóquela en DJANGO_SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(50))"

docker compose up --build
```

En otra terminal, siembre los datos iniciales y cree su usuario administrador:

```bash
docker compose exec web python manage.py sembrar_demo
docker compose exec web python manage.py createsuperuser
```

### Opción B — Entorno virtual con PostgreSQL propio

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Ajuste DATABASE_URL al host de su PostgreSQL, p. ej.:
#   DATABASE_URL=postgres://usuario:clave@localhost:5432/ircars

python manage.py migrate
python manage.py sembrar_demo
python manage.py createsuperuser
python manage.py runserver
```

### Accesos

| Recurso | URL |
|---|---|
| Caja (la pantalla de uso diario) | http://localhost:8000/ |
| Turno y corte | http://localhost:8000/turno/ |
| Métricas de operación | http://localhost:8000/reportes/metricas/ |
| Reporte de operación en PDF | http://localhost:8000/reportes/metricas/pdf/ |
| API (interfaz navegable de DRF) | http://localhost:8000/api/v1/ |
| Panel de administración | http://localhost:8000/admin/ |
| Sonda de proceso (*liveness*) | http://localhost:8000/healthz |
| Sonda de preparación (*readiness*) | http://localhost:8000/api/v1/salud/ |

---

## Pruebas

```bash
pytest                                                    # suite completa
pytest apps/estacionamiento/tests/test_tarifas.py         # solo el motor de cobro
pytest apps/estacionamiento/tests/test_corte.py           # solo el corte de caja
pytest apps/estacionamiento/tests/test_ajuste_corte.py    # solo la corrección del corte
pytest apps/estacionamiento/tests/test_metricas.py        # solo las métricas
```

La suite cubre **456 casos**, verificados contra **PostgreSQL 16 y SQLite**:

- **Motor de tarifas**: fronteras de cortesía, proporcionalidad, cobro mínimo,
  tope por día natural, precisión monetaria, validación de parámetros.
- **Motor de corte**: efectivo esperado, sobrante, faltante, cuadre exacto,
  cortesías, y la distinción entre «no se contó» y «se contó cero» —confundirlas
  ocultaría un faltante total—.
- **Casos de uso**: asignación de cajón, unicidad de folios y turnos, auditoría
  de la tarifa aplicada, validación del pago, adscripción del cobro al turno,
  inmutabilidad del corte cerrado.
- **Corrección del corte**: que la cifra declarada al cerrar sobreviva al
  ajuste, que el cajero no pueda corregir su propia caja, que dos correcciones
  se acumulen en lugar de pisarse, y que la base rechace un ajuste de cero o
  sin justificación aunque el servicio lo olvide.
- **Métricas**: que la hora se cuente en zona local —sin eso, una entrada de
  las 20:00 aparecería a las 02:00 y el pico señalaría una hora con la caseta
  cerrada—, que los empates de hora pico no se oculten, que los totales de
  clientes frecuentes no salgan en cero, y que el orden del ranking sea estable
  entre dos ejecuciones del mismo reporte.
- **Reporte en PDF**: que el archivo sea un PDF válido, que se descargue en
  lugar de abrirse, que no quede en ninguna caché, y que un periodo sin
  movimientos produzca documento en lugar de fallar.
- **Contrato de la API**: códigos de estado, permisos, autenticación.

Los motores de tarificación y de corte **no requieren base de datos**: son
lógica pura y corren en milisegundos.

> Probar contra PostgreSQL no es redundante. Un defecto real apareció solo ahí:
> al volverse opcional el cajón, `select_related` generó un `LEFT OUTER JOIN` y
> PostgreSQL rechazó el `FOR UPDATE`. En SQLite no se manifestó porque ahí
> `select_for_update` se ignora en silencio.

---

## API

Todos los endpoints requieren autenticación, excepto la sonda de salud.
Cada movimiento queda atribuido al operador que lo ejecutó.

| Método | Ruta | Operación |
|---|---|---|
| `POST` | `/api/v1/folios/` | Registrar entrada y emitir folio |
| `GET` | `/api/v1/folios/` | Consultar folios (`?placa=`, `?estado=`) |
| `GET` | `/api/v1/folios/{uuid}/` | Detalle de un folio |
| `GET` \| `POST` | `/api/v1/folios/{uuid}/cotizar/` | Importe a pagar, **sin cerrar** |
| `POST` | `/api/v1/folios/{uuid}/salida/` | Cobrar, cerrar y liberar el cajón |
| `POST` | `/api/v1/folios/{uuid}/cancelar/` | Cancelación con motivo documentado |
| `GET` | `/api/v1/ocupacion/` | Disponibilidad por tipo de cajón |
| `GET` | `/api/v1/cajones/` | Inventario de cajones |
| `*` | `/api/v1/tarifas/` | Cuadro tarifario (solo personal administrativo) |

### Ejemplo: ciclo completo

```bash
# 1. Entrada. La placa se normaliza a mayúsculas y sin espacios.
curl -u operador:clave -X POST http://localhost:8000/api/v1/folios/ \
     -H 'Content-Type: application/json' \
     -d '{"placa":"abc-123","tipo_cajon":"AUTO"}'

# 2. Cotizar antes de cobrar (no modifica el folio).
curl -u operador:clave http://localhost:8000/api/v1/folios/<uuid>/cotizar/

# 3. Cobrar y cerrar. Devuelve importe, cambio y desglose.
curl -u operador:clave -X POST http://localhost:8000/api/v1/folios/<uuid>/salida/ \
     -H 'Content-Type: application/json' -d '{"pago_recibido":"50.00"}'
```

### Códigos de respuesta

| Código | Situación |
|---|---|
| `400` | Datos inválidos, pago insuficiente, tarifa no configurada |
| `401` / `403` | Sin credenciales, o sin permiso para la operación |
| `404` | Folio inexistente |
| `409` | Conflicto de estado: placa ya dentro, sin cajones, folio ya cerrado |

---

## Motor de tarificación

Parámetros configurables desde el panel de administración, **nunca fijos en el
código**:

| Parámetro | Significado |
|---|---|
| `cuota_hora` | Precio de una hora completa |
| `minutos_cortesia` | Tolerancia sin costo (maniobras) |
| `fraccion_minutos` | Bloque mínimo facturable. `1` = cobro por minuto real |
| `cobro_minimo` | Piso del importe. No aplica dentro de la cortesía |
| `tope_diario` | Techo por cada día natural iniciado |

Orden de aplicación de las reglas:

1. **Cortesía** — dentro de la tolerancia, el importe es cero.
2. **Proporcional** — bloques de `fraccion_minutos`; el bloque iniciado se cobra.
3. **Cobro mínimo** — actúa como piso, salvo en cortesía.
4. **Tope diario** — acota el resultado anterior; prevalece sobre el mínimo.

Ejemplo con cuota de $30/h, cortesía de 15 min, fracción de 1 min y tope de $250:

| Permanencia | Importe | Regla aplicada |
|---|---|---|
| 15 min | $0.00 | Cortesía (frontera inclusiva) |
| 16 min | $8.00 | Proporcional por minuto |
| 90 min | $45.00 | Proporcional, sin redondeo a hora completa |
| 20 h | $250.00 | Tope diario |
| 25 h | $500.00 | Tope × 2 días naturales iniciados |

---

## Cumplimiento normativo

El diseño atiende el criterio de **cobro proporcional** aplicable a
estacionamientos en México (PROFECO y normatividad local):

- El cobro se calcula sobre el **tiempo real** de permanencia. Por omisión la
  fracción es de 1 minuto, es decir, proporcionalidad estricta.
- Los segundos se **truncan**, no se redondean: no se cobra un minuto que no
  transcurrió por completo.
- La tarifa vive en base de datos con **vigencia y registro histórico**, y se
  aplica la que estaba vigente **al momento de la entrada** —el precio exhibido
  cuando el cliente ingresó—, no la del momento del cobro.
- Al cerrar el folio, los parámetros tarifarios se **copian al propio folio**.
  Si la tarifa cambia después, el folio sigue explicando su importe. El cobro
  es reproducible y auditable de forma permanente.
- Cada cobro conserva un **desglose** del cálculo, base de un comprobante
  explicable ante una aclaración o una verificación.
- El **boleto consultable por QR** exhibe el cuadro tarifario vigente al
  ingresar, con la leyenda de que es el que se aplicará aunque el precio
  cambie durante la estancia. El importe que ve el cliente sale del mismo
  motor que usa la caja, de modo que no puede diferir del que se le cobre.

En materia de datos personales (LFPDPPP), el boleto público aplica
**minimización** —la placa se enmascara al presentar y la completa nunca sale
de la base— y **temporalidad**: el enlace de un boleto ya liquidado deja de
responder pasadas las horas configuradas, de modo que una dirección que vive
en historiales y mensajes reenviados no sigue exhibiendo datos, mientras el
historial contable permanece íntegro.

---

## Decisiones de ingeniería

**Integridad en la base de datos, no solo en el código.** Las reglas "un cajón
no admite dos folios abiertos" y "una placa no entra dos veces sin salir" son
índices únicos parciales de PostgreSQL. Si dos casetas registran una entrada en
el mismo milisegundo, la base rechaza la segunda. Validar únicamente en Python
dejaría abierta una condición de carrera.

**Concurrencia.** La asignación de cajón usa `select_for_update(skip_locked=True)`:
cada caseta toma un cajón distinto sin esperar a la otra. El cobro usa
`select_for_update` con espera, porque el folio es único y la segunda
transacción debe observar el estado ya cerrado — así no existe doble cobro.

**Importes en `Decimal`, nunca `float`.** El punto flotante introduce errores de
representación inaceptables en montos monetarios.

**Los folios no se editan ni se borran.** Son registros contables. El `ViewSet`
no expone `update` ni `destroy`, y el panel de administración los muestra en
solo lectura. Las correcciones se realizan por cancelación con motivo
documentado, operador identificado y registro en el log.

**Un corte firmado tampoco se reescribe: se contra-asienta.** La misma regla
aplicada al control de caja. Cuando un turno se cierra con una cifra
equivocada, la corrección se guarda como asiento aparte —con motivo,
justificación escrita y autor— y el comprobante pasa a mostrar las dos cifras.
Permitir editar el declarado habría sido más simple y habría desarmado el
control: una diferencia que puede borrarse sin dejar rastro no disuade a nadie
de provocarla. La contrapartida —dos cifras en el comprobante en lugar de una—
es el precio de que la primera siga significando algo.

**Mínimo privilegio.** El cuadro tarifario solo es modificable por personal
administrativo: el precio no se toca desde una caja. El contenedor no corre
como `root`.

**La interfaz también sostiene el control.** Las restricciones de negocio no se
confían solo al código: la pantalla está hecha para no invitar a saltárselas. La
cabecera no exhibe ningún importe del turno abierto, porque haría inútil el
cierre ciego. Cotizar y cobrar son dos botones y no uno, para que un error de
captura no produzca un folio cerrado e inmutable. El único botón rojo de la
aplicación es el corte de caja, que es la acción irreversible. Y el estado que
impide operar —no tener turno— se muestra de forma permanente, no cuando ya
falló la operación.

**Observabilidad (SRE).** Dos sondas distintas: `/healthz` responde si el
proceso vive, `/api/v1/salud/` verifica la base de datos y reporta `503` cuando
el equipo no está en condiciones de operar. Cada evento de negocio —entrada,
cobro, cancelación— se registra con su folio para poder trazar cualquier
movimiento.

**Configuración por entorno (12-factor).** El mismo código corre en su laptop,
en el equipo del estacionamiento y en un servidor; solo cambia el `.env`. No
hay credenciales en el repositorio.

---

## Pendientes sugeridos

Lo construido es la base de operación. Las siguientes extensiones se apoyan en
los mismos servicios, sin duplicar reglas de negocio:

- **Pensiones mensuales** y validación de cortesías por comercio.
- **Controlador de hardware**: pluma, sensores de cajón, lector de QR.
- **Impresión directa a la térmica.** El comprobante de corte ya se genera en
  texto plano y se guarda en archivo; falta enviarlo a la impresora sin pasar
  por el explorador, y emitir boleto de entrada y recibo de cobro con el
  desglose que el folio ya persiste.
- **Cobrar sin conexión.** Es el pendiente de fondo de operar en la nube. El
  primer paso acordado es un archivo de vectores de prueba que fije la
  equivalencia entre el motor de tarifas en Python y una réplica en
  JavaScript: sin esa equivalencia demostrada, una caja que cobre en el
  navegador cobraría distinto que el servidor.

Ya no figuran aquí los puntos que sí están construidos: el **corte de caja**
por turno y operador —con cierre ciego, conciliación, historial y corrección
auditable de un cierre equivocado—, el **panel de caja**, que es la propia
aplicación web, la **consulta del boleto por el cliente** y el **reporte de
operación** con horas pico, clientes frecuentes y salida en PDF, los tres
descritos arriba.

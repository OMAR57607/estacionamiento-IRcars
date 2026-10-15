# Controlador de Estacionamiento — IRcars

Sistema de control de estacionamiento con cobro por hora y **fracción
proporcional**, control de turnos y **corte de caja con cierre ciego**.

## Modo de operación: ejecutable local

La caja opera con el **ejecutable de escritorio** en el equipo de la caseta,
con SQLite en ese mismo equipo. Para un estacionamiento de 13–15 autos al día
—unas 30 operaciones diarias— es la arquitectura correcta, y no por falta de
ambición técnica:

- **Sigue cobrando sin internet.** Si se cae la red, la caseta no se detiene.
  Ninguna arquitectura en la nube ofrece eso.
- **Sin exposición.** No hay puertos abiertos ni servicios accesibles desde
  fuera.
- **Sin costo mensual** ni dependencia de un proveedor.
- **Un solo punto de falla** en lugar de dos.

A ese volumen, SQLite acumula unos 5,500 folios al año: una fracción mínima de
su capacidad. La complejidad de un motor cliente-servidor —agrupadores de
conexiones, réplicas, respaldos gestionados— no aportaría nada aquí.

### Las otras dos interfaces siguen disponibles

El repositorio conserva una **interfaz web** y una **API REST** completas,
sobre los mismos servicios y el mismo motor de cobro. No están desplegadas hoy,
pero funcionan: si en el futuro operan varias casetas o quiere consultar la
caja desde fuera, está hecho. Ver [DESPLIEGUE-RAILWAY.md](DESPLIEGUE-RAILWAY.md).

Las tres interfaces comparten exactamente las mismas reglas: el motor de
tarificación y los casos de uso se escriben una vez y las tres los heredan. Un
cambio en el cobro o en el control de caja no se replica en tres lugares.

---

## Obtener el ejecutable de Windows

PyInstaller no hace compilación cruzada, así que el `.exe` se compila en un
runner de Windows mediante GitHub Actions. **No necesita instalar Python ni
herramientas de compilación.**

1. Entre a la pestaña **Actions** del repositorio.
2. Abra la ejecución más reciente de *«Compilar ejecutable de Windows»*.
3. Descargue el artefacto **`IRcars-Caja-windows`**.
4. Descomprima y ejecute `IRcars-Caja.exe`.

El flujo se dispara solo cuando cambia algo que afecta al ejecutable, y también
puede lanzarse a mano desde esa misma pestaña.

Antes de publicar el artefacto, el flujo **ejecuta el propio `.exe`** con
`--verificar`: arranca Django dentro del binario, aplica las migraciones,
resuelve la zona horaria y comprueba los motores de tarificación y de corte con
importes conocidos. Si PyInstaller dejó algo fuera, la compilación falla ahí y
no le entrega un ejecutable roto.

### Primer uso

En el primer arranque no existe ningún operador, así que la aplicación le guía
para crear el usuario administrador inicial. Después:

1. Pestaña **Tarifa** → registre el cuadro tarifario (sin tarifa vigente no se
   puede cobrar).
2. Pestaña **Turno y corte** → abra turno declarando el fondo inicial.
3. Pestaña **Caja** → ya puede registrar entradas y cobrar salidas.

Los datos viven en `%LOCALAPPDATA%\IRcars`, ruta que la propia aplicación
muestra en su barra inferior —un clic la copia al portapapeles—.
**Respaldar el sistema es copiar esa carpeta.**

### La pantalla de caja

El ciclo de un vehículo son cuatro gestos, y la pantalla está dispuesta para
que ocurran sin buscar nada:

1. **Entrada.** Se teclea la placa y se pulsa `Enter`. El acuse confirma la
   hora —y el cajón, si hay inventario de cajones dado de alta—, y el cursor
   vuelve al campo listo para el siguiente vehículo.
2. **Cotización.** Placa en «Salida y cobro» y `Enter`. El importe aparece en
   grande, con la permanencia y la tarifa aplicada debajo. **No cierra el
   folio**: todavía puede corregirse.
3. **Cobro.** Al teclear el pago recibido, **el cambio se calcula al
   instante** —o avisa cuánto falta—, a tiempo de contar los billetes. Los
   botones de billete y de importe exacto evitan teclear la cifra.
4. **Cierre.** `F5` cobra, cierra el folio y libera el cajón.

Dos ayudas para los casos incómodos:

- **Quien perdió el boleto.** El tablero de vehículos dentro tiene buscador:
  se teclean unos caracteres de la placa y, en cuanto queda una sola
  coincidencia, `Enter` la cotiza —doble clic sobre cualquier fila hace lo
  mismo—. La columna «Lleva acumulado» responde «¿cuánto llevo?» sin tener que
  cotizar folio por folio.
- **Si no hay turno abierto**, la cabecera lo advierte en rojo desde cualquier
  pestaña y lleva a abrirlo de un clic. Es la única condición que impide
  cobrar, y se ve antes de tener al cliente enfrente.

La cabecera **nunca muestra importes del turno en curso**. Es el cierre ciego:
si el total estuviera a la vista, el conteo físico se ajustaría a él.

### Atajos de teclado

Pensados para operar sin ratón con un cliente esperando. Están impresos de
forma permanente en la barra inferior, que es como se aprenden:

| Tecla | Acción |
|---|---|
| `F2` | Registrar entrada |
| `F4` | Cotizar salida |
| `F5` | Cobrar y cerrar folio |
| `F9` | Actualizar el tablero |
| `Enter` | Confirma el campo en que esté el cursor |

---

## Arquitectura

Las dependencias apuntan siempre hacia el dominio. El núcleo de negocio no
conoce Django, HTTP ni la base de datos, por lo que es verificable de forma
aislada y reutilizable por cualquier interfaz futura.

```
  web/views.py      Interfaz web de caja (plantillas Django)
  views.py          API REST (DRF)                    ─┐
  escritorio/       Aplicación de escritorio (Tkinter) ─┤ tres interfaces,
  api_errors.py     Errores de dominio → HTTP          ─┘ cero reglas propias
        │
        ▼
  services.py       Casos de uso: entrada, cotización, cobro, cancelación
  services_caja.py  Casos de uso: apertura de turno, cierre ciego, corte
        │
        ▼
  domain/tarifas.py Motor de tarificación   ─┐ lógica pura, sin Django,
  domain/corte.py   Motor de corte de caja  ─┤ sin ORM, sin reloj propio
  domain/exceptions.py                       ─┘
        │
        ▼
  models.py         Persistencia e integridad (PostgreSQL / SQLite)
```

| Capa | Archivos | Responsabilidad |
|---|---|---|
| Interfaz | `web/`, `views.py`, `serializers.py`, `escritorio/` | Deserializar, delegar, presentar |
| └ Presentación | `escritorio/tema.py` | Color, tipografía y componentes de la app de escritorio |
| Aplicación | `services.py`, `services_caja.py` | Orquestar casos de uso, transacciones, concurrencia |
| Dominio | `domain/tarifas.py`, `domain/corte.py` | Calcular importes y diferencias. Sin framework, 100 % testeable |
| Infraestructura | `models.py`, `migrations/` | Persistir y hacer cumplir la integridad |

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
| API (interfaz navegable de DRF) | http://localhost:8000/api/v1/ |
| Panel de administración | http://localhost:8000/admin/ |
| Sonda de proceso (*liveness*) | http://localhost:8000/healthz |
| Sonda de preparación (*readiness*) | http://localhost:8000/api/v1/salud/ |

---

## Pruebas

```bash
pytest                                              # suite completa
pytest apps/estacionamiento/tests/test_tarifas.py   # solo el motor de cobro
pytest apps/estacionamiento/tests/test_corte.py     # solo el corte de caja
```

La suite cubre **91 casos**, verificados contra **PostgreSQL 16 y SQLite**:

- **Motor de tarifas**: fronteras de cortesía, proporcionalidad, cobro mínimo,
  tope por día natural, precisión monetaria, validación de parámetros.
- **Motor de corte**: efectivo esperado, sobrante, faltante, cuadre exacto,
  cortesías, y la distinción entre «no se contó» y «se contó cero» —confundirlas
  ocultaría un faltante total—.
- **Casos de uso**: asignación de cajón, unicidad de folios y turnos, auditoría
  de la tarifa aplicada, validación del pago, adscripción del cobro al turno,
  inmutabilidad del corte cerrado.
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
- **Sincronización opcional a la nube** para respaldo y reportería central,
  manteniendo la operación local como fuente de verdad.

Ya no figuran aquí dos puntos que sí están construidos: el **corte de caja**
por turno y operador —con cierre ciego, conciliación e historial— y el **panel
de caja**, que es la propia aplicación de escritorio.

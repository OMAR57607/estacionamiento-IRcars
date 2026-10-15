# Controlador de Estacionamiento — IRcars

Sistema de control de estacionamiento con cobro por hora y **fracción
proporcional**, control de turnos y **corte de caja con cierre ciego**.

Tres interfaces sobre un mismo núcleo de negocio:

| Interfaz | Para qué | Dónde corre |
|---|---|---|
| **Web de caja** | Operación diaria: entrada, cobro, turno, corte, reportes | Railway + Supabase |
| **API REST** | Integraciones y consulta programática | Railway + Supabase |
| **Ejecutable de escritorio** | Caja autónoma sin red, con SQLite local | Equipo en la caseta |

Las tres comparten exactamente las mismas reglas: el motor de tarificación y
los casos de uso se escriben una vez y las tres los heredan. Un cambio en el
cobro o en el control de caja no se replica en tres lugares.

**Despliegue en operación:**
`https://estacionamiento-ircars-production.up.railway.app`
(ver [DESPLIEGUE-RAILWAY.md](DESPLIEGUE-RAILWAY.md))

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

- **Corte de caja** por turno y operador, con totales y conciliación.
- **Panel de caja** en Streamlit sobre los servicios existentes.
- **Pensiones mensuales** y validación de cortesías por comercio.
- **Controlador de hardware**: pluma, sensores de cajón, lector de QR.
- **Impresión de boleto y comprobante** a partir del desglose ya persistido.
- **Sincronización opcional a la nube** para respaldo y reportería central,
  manteniendo la operación local como fuente de verdad.

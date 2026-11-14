# Rediseño de la caja y módulo de pensiones — especificación de ejecución

Documento de arquitectura para quien implemente el cambio. Está escrito para
ejecutarse **por fases, un commit por fase, con la suite en verde al cerrar
cada una**. La referencia visual es [`docs/rediseno/maqueta.html`](rediseno/maqueta.html):
ábrala en el navegador antes de tocar una plantilla.

- **Alcance:** rediseño de interfaz y experiencia de la caja web, y alta,
  cobro y control de acceso de vehículos en **pensión** (mensualidad).
- **Fuera de alcance:** API REST (salvo un campo de lectura, §6.8), cobro sin
  conexión, impresión térmica, cajón fijo por pensionado, pagos con tarjeta o
  transferencia.

---

## 0. Reglas para quien ejecuta

1. **Lea antes de escribir.** `README.md` (sección «Decisiones de ingeniería»),
   `apps/estacionamiento/models.py`, `services.py`, `services_caja.py`,
   `domain/corte.py`, `web/views.py`, `static/estacionamiento/estilos.css` y
   `tests/test_apariencia.py`.
2. **La dirección de dependencias no cambia:** `web/` → `services*.py` →
   `domain/` → nada. El dominio no importa Django. Ninguna regla de negocio
   vive en una vista ni en una plantilla.
3. **Los comentarios existentes no se borran.** Explican decisiones; si una
   decisión cambia, el comentario se actualiza diciendo por qué. El código
   nuevo se comenta con el mismo criterio: el porqué, no el qué. Todo en
   español, como el resto del repositorio.
4. **Dinero en `Decimal`**, redondeo con el `_redondear` existente. Nunca `float`.
5. **Nada se edita ni se borra si tuvo efecto económico:** se desactiva, se
   cancela con motivo o se contra-asienta. Aplica a planes, pensiones y pagos.
6. **Integridad en la base además de en el código:** cada invariante de §6.2
   lleva su `UniqueConstraint`/`CheckConstraint`.
7. **Sin dependencias nuevas** en `requirements.txt`, sin CDN, sin fuentes
   remotas, sin framework de JavaScript.
8. **El JavaScript es mejora progresiva.** Toda operación debe completarse con
   JavaScript desactivado; el servidor es la única autoridad sobre importes.
9. **El cierre ciego no se debilita:** ninguna pantalla del operador muestra
   importes acumulados del turno abierto, tampoco los de pensiones.
10. **Antes de cada commit:** `python manage.py makemigrations --check --dry-run`,
    `python manage.py check` y `pytest`. Una prueba que falle por un cambio
    deliberado de interfaz se actualiza junto con su docstring; no se omite ni
    se elimina.
11. **Búsquedas insensibles a mayúsculas** en todo lo nuevo (placa, nombre,
    teléfono). La placa se normaliza con `services.normalizar_placa`.
12. Si una decisión de negocio de §2 no está confirmada, implemente el valor
    por omisión indicado y déjelo como ajuste en `settings`, no como constante
    enterrada.

---

## 1. Diagnóstico de la interfaz actual

| # | Problema observado | Dónde | Consecuencia en la caseta |
|---|---|---|---|
| P1 | Dos campos de placa (Entrada / Salida) | `caja.html` | En teléfono quedan apilados; el operador teclea en el equivocado y recibe un error en lugar de la operación |
| P2 | Tipo de vehículo en un `<select>` | `EntradaForm` | Dos toques y un menú nativo para una elección binaria que decide el precio |
| P3 | El patio no se puede filtrar | `caja.html` | Con 40 vehículos, localizar una placa es desplazar la lista. `CASETA.md` promete «con teclear tres o cuatro caracteres basta» y la pantalla no lo cumple |
| P4 | El cambio a entregar llega como un aviso de texto | `_procesar_cobro` | La cifra que se dice en voz alta tiene el tamaño de cualquier mensaje |
| P5 | El pago se teclea siempre completo | `CobroForm` | El 90 % de los pagos son un billete; no hay atajos |
| P6 | Métricas no existe en la navegación del teléfono; Reportes y Equipo estrechan la barra a cinco destinos | `base.html` | El dueño no llega a Métricas desde el móvil; los objetivos táctiles se encogen |
| P7 | La cabecera azul no corresponde a la marca | `estilos.css` | El logotipo RJ CARS es grafito y plata; la aplicación parece de otro negocio |
| P8 | Errores de formulario genéricos («Revise la placa capturada») | vistas | El operador no sabe qué corregir |
| P9 | No hay forma de registrar un pensionado | — | El cliente de mensualidad se cobra a mano fuera del sistema y su dinero no entra al corte |
| P10 | Estilos en línea (`style="margin-top:.7rem"`) repartidos | plantillas | Espaciado inconsistente e imposible de ajustar desde la hoja |

Lo que **ya está bien y se conserva**: teléfono primero, objetivos de 48 px,
tema claro/oscuro por tokens, cotizar y cobrar en dos pasos, el rojo reservado
al corte, el estado del turno siempre visible, listas que se apilan sin
duplicar marcado.

---

## 2. Decisiones de negocio — confirmadas

Confirmadas por el dueño el 14/11/2026. **Sustituyen a la propuesta mensual
original**: la pensión es **diaria** y su vigencia se mide en horas y minutos,
no en fechas. Donde el resto de este documento dice «mensualidad», «mes» o
`DateField`, rige lo de esta sección.

| Id | Decisión | Regla implementada |
|---|---|---|
| D1 | Precio | **$180.00 por día** (24 horas). Un solo plan activo para cualquier tipo de vehículo; lo siembra la migración y solo el perfil administrativo lo cambia, con contraseña |
| D2 | Periodo | Cada día pagado cubre 24 horas. Se pueden pagar varios días a la vez (1 a 31) |
| D3 | Vencimiento | **Si la estancia rebasa la vigencia en un minuto completo, ese excedente se cobra con la tarifa por hora vigente**, sin minutos de cortesía (la cortesía es para quien entra, no para quien se pasa). Los segundos se truncan, como en todo cobro |
| D4 | «A menos que renueve» | Renovar cubre la estancia en curso sin huecos: si la pensión está vigente, el periodo nuevo empieza donde acaba el anterior; si venció y el vehículo está dentro, empieza al vencimiento (o a la entrada, si entró ya vencido) y la estancia pasa a ser de pensión; si no está dentro, empieza al pagar |
| D5 | Vehículos por pensión | Uno |
| D6 | Forma de pago | Solo efectivo, dentro de un turno abierto |
| D7 | Permisos | Los actuales: el operador da de alta, cobra días y registra entradas y salidas; el perfil administrativo cambia el precio, da de baja y cancela un pago |
| D8 | Aviso | La pensión se marca «Por vencer» en sus últimas 2 horas (`IRCARS_PENSION_AVISO_HORAS`) |

Consecuencias en la arquitectura de §6:

- `Pension.vigente_hasta` y `PagoPension.periodo_desde/hasta` son `DateTimeField`.
- `PlanPension.precio_diario` sustituye a `precio_mensual`; `PagoPension.dias`
  sustituye a `meses`.
- El dominio trabaja con `datetime` y `timedelta(days=…)`; desaparecen
  `sumar_meses` y los días de gracia (D4 los hace innecesarios).
- Un folio de pensión que sale **dentro** de la vigencia se cierra en $0, sin
  turno. Si sale **fuera**, el excedente se cobra como cualquier boleto:
  exige turno, entra al corte como cobro de boleto y su folio copia los
  parámetros de tarifa aplicados.

**Marco legal que el diseño ya atiende** (PROFECO / LFPC y LFPDPPP):

- El precio de la mensualidad es el del **plan vigente al momento del pago** y
  se copia al pago. Un cambio de precio no altera periodos ya pagados; rige
  desde la siguiente renovación.
- Ningún cargo que no esté en el plan exhibido: no hay recargos, penalizaciones
  ni «reactivaciones» automáticas. Vencida la pensión, el cliente paga la
  tarifa por hora exhibida, que es la de cualquier usuario.
- Cada pago genera un **recibo** con periodo cubierto, importe, plan, fecha y
  operador (pantalla imprimible, §7.6).
- Nombre y teléfono del pensionado son datos personales: solo los ve personal
  con sesión, nunca viajan al boleto público, no se escriben en la bitácora y
  la pantalla de alta enlaza el aviso de privacidad si
  `IRCARS_AVISO_PRIVACIDAD_URL` está configurado.

---

## 3. Principios de experiencia

1. **Una placa, un campo.** El operador teclea lo que ve en el parabrisas; el
   sistema sabe si ese vehículo está dentro, es pensionado o es nuevo.
2. **La elección frecuente es un toque.** Tipo de vehículo, billetes de pago y
   periodos son botones, no menús.
3. **La cifra que se dice en voz alta es la más grande de la pantalla:**
   importe al cotizar, cambio al cobrar.
4. **El estado se lee sin color:** toda etiqueta lleva texto («PENSIÓN»,
   «VENCIDA», «FALTANTE»). El color refuerza, no informa.
5. **El pensionado no hace fila:** su entrada y salida no pasan por QR, cobro
   ni turno.
6. **Lo irreversible cuesta un paso más y lo deliberado vive en su propia
   dirección** (patrón ya usado en cambio de precio y ajuste de corte).
7. **Cuatro destinos en el pulgar**, el resto en «Más».

---

## 4. Sistema de diseño

Todo vive en `static/estacionamiento/estilos.css`, que se reorganiza en este
orden: tokens → base → estructura → componentes → pantallas → impresión. No se
crea una segunda hoja.

### 4.1 Tokens de color

La identidad pasa a **grafito y plata** (el logotipo). El azul se conserva como
color de **acción**: no se cambia por el rojo vino de la marca porque el rojo
ya significa «error / irreversible» y la paleta de gráficas verificada depende
de ese azul.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--marca` | `#1f2128` | `#15171c` | Cabecera, pantalla de acceso |
| `--sobre-marca` | `#f2f3f5` | `#e8eaee` | Texto sobre `--marca` |
| `--marca-plata` | `#c9ccd3` | `#a9adb6` | Filetes y detalles de marca |
| `--tinta` | `#16181d` | `#e9ebef` | Texto |
| `--tinta-suave` | `#5b616c` | `#9da3ae` | Texto secundario |
| `--fondo` | `#f3f4f6` | `#0e1014` | Lienzo |
| `--superficie` | `#ffffff` | `#191c22` | Tarjetas |
| `--superficie-alt` | `#f8f9fa` | `#20242b` | Encabezados de tabla, campos de solo lectura |
| `--borde` / `--borde-fuerte` | `#dcdfe4` / `#b9bec7` | `#2c313a` / `#434a56` | |
| `--acento` / `--acento-oscuro` / `--acento-tenue` / `--sobre-acento` | sin cambio | sin cambio | Acción primaria |
| `--exito*`, `--alerta*`, `--error*` | sin cambio | sin cambio | Estados |
| `--pension` | `#5b3fa8` | `#b8a4f2` | Etiqueta y filete de pensión |
| `--pension-fondo` | `#efeafb` | `#231c3b` | Fondo de la etiqueta |
| `--viz-*` | sin cambio | sin cambio | Gráficas (no tocar: están verificadas) |

Reglas: contraste mínimo 4.5:1 texto / 3:1 componentes en ambos temas
(compruébelo al fijar valores); ningún color en atributos `style` (lo vigila
`test_ninguna_plantilla_fija_colores_en_linea`); el marco del QR sigue con
`background: #fff`. Actualice los dos `<meta name="theme-color">` de
`base.html` y el `theme_color`/`background_color` del manifiesto a `--marca`.

### 4.2 Escalas

```
Espacio   --e-1: .25rem  --e-2: .5rem  --e-3: .75rem  --e-4: 1rem  --e-5: 1.5rem  --e-6: 2rem
Texto     --t-xs: .75rem  --t-s: .875rem  --t-m: 1rem  --t-l: 1.25rem  --t-xl: 1.75rem  --t-cifra: 3rem
Radio     --radio-chico: 8px  --radio: 12px  --radio-pildora: 999px
Táctil    --toque: 48px  --toque-grande: 56px
```

El cuerpo se queda en 16 px (evita el zoom de iOS al enfocar). Fuente del
sistema; cifras con `font-variant-numeric: tabular-nums`; placas en monoespaciada.
Sustituya los `style="margin…"` de las plantillas por utilidades `.pila`
(`display:grid; gap:var(--e-3)`) y `.fila` (`display:flex; gap:var(--e-2)`).

### 4.3 Componentes

Se conservan y se retocan con los tokens nuevos: `.barra`, `.barra-inferior`,
`.tarjeta`, `.campo`, `.boton`, `.aviso`, `.lista*`, `.indicador`, `.etiqueta`,
tablas. Se añaden:

| Componente | Clase | Especificación |
|---|---|---|
| Control segmentado | `.segmentado` | `fieldset` con `input[type=radio]` ocultos visualmente + `label`. Alto `--toque`, opción elegida con `--acento-tenue` y borde `--acento`. Navegable con flechas (nativo de radio) |
| Botón doble | `.acciones-dobles` | Rejilla de dos columnas iguales; botones de `--toque-grande` |
| Botón fantasma | `.boton.fantasma` | Fondo transparente, borde `--borde-fuerte`, texto `--tinta`. Reemplaza a `.secundario` en acciones de segundo orden |
| Píldora de estado | `.pildora` + `.pension` `.vigente` `.por-vencer` `.vencida` `.baja` | Texto en mayúsculas `--t-xs`, siempre con palabra |
| Atajos de pago | `.atajos` | Fila de botones `type="button"` con `data-monto`; envuelve en dos renglones |
| Cifra | `.cifra` (+ `.cifra.cambio`) | `--t-cifra`, peso 700. Sustituye a `.importe-destacado` (mantenga el alias hasta migrar todas las plantillas) |
| Hoja de resultado | `.tarjeta.resultado` | Tarjeta de confirmación tras cobrar: rótulo, cifra, acción «Siguiente vehículo» |
| Filtro de lista | `.filtro` | Campo con `type="search"`; contador «3 de 41» con `aria-live="polite"` |
| Menú «Más» | `.menu` | Lista de enlaces de `--toque-grande` con icono SVG, rótulo y descripción |
| Estado vacío | `.vacio` | Texto + acción sugerida, no solo «No hay…» |

Accesibilidad transversal: `aria-current="page"` en el destino activo de ambas
navegaciones; contenedor de mensajes con `role="status"` (y `role="alert"` para
`error`); errores de campo junto al campo con `aria-describedby`; enlace
«Saltar al contenido» al inicio; todo SVG decorativo con `aria-hidden="true"`
y `stroke="currentColor"`.

### 4.4 JavaScript

Un solo archivo, `static/estacionamiento/caja.js`, cargado con `defer` desde
`base.html`. Sin dependencias, sin estado propio, menos de ~120 líneas.
Comportamientos, cada uno activado por un atributo `data-`:

| Atributo | Efecto | Sin JavaScript |
|---|---|---|
| `data-filtra="#patio"` | Oculta filas cuya `data-placa` no contiene el texto (normalizado: mayúsculas, sin espacios ni guiones) y actualiza el contador | La lista se muestra completa |
| `data-monto` | Escribe el monto en «Pago recibido» (`exacto` usa `data-importe`) | Se teclea el pago |
| `data-cambio` | Muestra «Cambio: $X» o «Faltan $X» mientras se teclea. Rotulado como estimación; el cambio oficial es el del servidor | No aparece |
| `data-mayusculas` | Normaliza la placa al teclear | El servidor normaliza igual |
| `data-confirma="texto"` | `confirm()` en acciones de baja/cancelación | La vista de confirmación del servidor sigue existiendo |

El aritmético del cambio se hace en centavos enteros, nunca con flotantes.
El script en línea de `boleto.html` se queda donde está.

---

## 5. Arquitectura de información

### 5.1 Navegación

| Destino | Teléfono (barra inferior) | Escritorio (cabecera) | Quién |
|---|---|---|---|
| Caja `/` | ✔ | ✔ | Todos |
| Pensiones `/pensiones/` | ✔ | ✔ | Todos |
| Turno `/turno/` | ✔ | ✔ | Todos |
| Más `/mas/` | ✔ | — (sus destinos van en la cabecera) | Todos |
| Precio, Reportes, Métricas, Equipo, Mantenimiento, Mi contraseña, Salir | dentro de «Más» | cabecera (como hoy) | Según perfil |

- «Más» es una **página** (`web:mas`, plantilla `mas.html`), no un menú
  desplegable: funciona sin JavaScript y el botón «atrás» hace lo esperado.
- La cabecera del teléfono queda en: logotipo-nombre · píldora del turno
  (enlace a `/turno/`, «Turno 128» / «Sin turno»). «Salir» se muda a «Más» en
  teléfono y permanece en la cabecera en escritorio.
- La cabecera de escritorio **sigue enlazando `web:tarifa`**, de modo que
  `TestNavegacion.test_hay_navegacion_al_alcance_del_pulgar` continúa pasando;
  añada a esa prueba `web:pensiones` y `web:mas`.

### 5.2 Mapa de pantallas

```
/                         Caja (rediseñada, §7.1–7.3)
/pensiones/               Lista con filtro y estados (§7.4)
/pensiones/nueva/         Alta + primer pago opcional (§7.5)
/pensiones/<id>/          Ficha: vigencia, pagos, acciones (§7.6)
/pensiones/<id>/cobrar/   Cobro de mensualidad (dos pasos)
/pensiones/pago/<id>/     Recibo imprimible
/pensiones/planes/        Planes y precio (administrativo, con contraseña)
/mas/                     Menú de secciones
(resto de rutas: sin cambio)
```

---

## 6. Módulo de pensiones — arquitectura

### 6.1 Ubicación

Dentro de la app `estacionamiento` (comparte `Turno`, `Folio` y el corte; una
app aparte obligaría a dependencias cruzadas):

```
domain/pensiones.py        Periodos, vigencia y estado. Puro, sin Django
services_pensiones.py      Casos de uso transaccionales
web/views_pensiones.py     Vistas (views.py ya tiene 1 600 líneas: no crece)
web/forms.py               Formularios nuevos al final del archivo
templates/estacionamiento/pensiones/*.html
tests/test_pensiones_dominio.py · test_pensiones_servicios.py
      test_pensiones_web.py · test_pensiones_corte.py
migrations/0008_pensiones.py
```

### 6.2 Modelos

**`PlanPension`** — cuadro de precios, mismo patrón que `Tarifa`.

| Campo | Tipo | Nota |
|---|---|---|
| `nombre` | `CharField(100)` | «Pensión mensual automóvil» |
| `tipo_vehiculo` | `TipoVehiculo` | |
| `precio_mensual` | `Decimal(10,2)`, ≥ 0.01 | |
| `activo` | `Boolean` | |
| `vigente_desde`, `creado_en` | `DateTime` | |

Restricción: `UniqueConstraint(tipo_vehiculo, nombre) where activo`. Los planes
no se editan: cambiar el precio desactiva el plan y crea otro (reutilice el
flujo de autorización por contraseña de `tarifa_cambiar`).

**`Pension`** — el contrato de un vehículo.

| Campo | Tipo | Nota |
|---|---|---|
| `placa` | `CharField(15)`, indexado | Normalizada en `save()` como `Folio` |
| `tipo_vehiculo` | `TipoVehiculo` | |
| `plan` | FK `PlanPension`, `PROTECT` | Plan actual; el precio cobrado se copia a cada pago |
| `nombre_cliente` | `CharField(120)` | Dato personal |
| `telefono` | `CharField(20)`, opcional | Dato personal. Solo dígitos al guardar |
| `descripcion_vehiculo` | `CharField(80)`, opcional | «Jetta gris» — ayuda a reconocerlo |
| `estado` | `ACTIVA` / `BAJA` | |
| `vigente_hasta` | `DateField`, nulo | **Desnormalizado**: último día cubierto por pagos vigentes. Nulo = nunca pagó |
| `alta_en`, `alta_por` | `DateTime`, FK usuario | |
| `baja_en`, `baja_por`, `motivo_baja` | nulos / texto | |
| `notas` | `TextField` | |

Restricciones e índices:
- `UniqueConstraint(placa) where estado = ACTIVA` → `uq_pension_activa_por_placa`.
- `CheckConstraint`: `estado = ACTIVA` **o** (`baja_en` no nulo **y** `motivo_baja ≠ ''`).
- Índice `(estado, vigente_hasta)` para la lista y los avisos.

`vigente_hasta` es redundante respecto a los pagos, a propósito y por el mismo
motivo que la fotografía del corte: cada entrada de vehículo consulta la
vigencia y no debe agregar pagos. Solo lo escribe `services_pensiones`.

**`PagoPension`** — el asiento de dinero.

| Campo | Tipo | Nota |
|---|---|---|
| `pension` | FK, `PROTECT` | |
| `periodo_desde`, `periodo_hasta` | `DateField` | Inclusivos, fecha local |
| `monto` | `Decimal(10,2)` | Copia de `plan.precio_mensual` × meses |
| `meses` | `PositiveSmallInteger` (1–12) | |
| `plan`, `plan_nombre`, `plan_precio_mensual` | FK + copia | Reproducibilidad, como la copia de tarifa en `Folio` |
| `pago_recibido`, `cambio` | `Decimal`, nulos | |
| `turno` | FK `Turno`, `PROTECT`, **no nulo** | El dinero siempre cae en un corte |
| `operador` | FK usuario | |
| `estado` | `VIGENTE` / `CANCELADO` | |
| `cancelado_en`, `cancelado_por`, `motivo_cancelacion` | nulos / texto | |
| `uuid` | `UUIDField` único | Identificador del recibo |
| `creado_en` | `DateTime` | |

Restricciones: `periodo_hasta ≥ periodo_desde`; `monto > 0`; cancelado ⇒
motivo y fecha; índice `(turno, estado)` y `(pension, periodo_hasta)`.

**`Folio`** — un campo nuevo:

```python
pension = models.ForeignKey("Pension", on_delete=models.PROTECT, null=True,
                            blank=True, related_name="folios")
```

Un folio con `pension` es una estancia cubierta por mensualidad: se cierra con
`importe = 0`, **sin turno** y con `desglose = {"regla": "pension", …}`.

**`Turno`** — dos campos de fotografía, nulos: `total_pensiones`
(`Decimal(12,2)`) y `pagos_pension` (`PositiveInteger`).

### 6.3 Dominio (`domain/pensiones.py`)

Funciones puras; reciben fechas, nunca leen el reloj.

```python
class EstadoVigencia(str, Enum): SIN_PAGO, VIGENTE, POR_VENCER, VENCIDA

def sumar_meses(fecha: date, meses: int) -> date
    # Aniversario con tope al último día del mes (31 ene + 1 → 28/29 feb).

def periodo_siguiente(*, hoy: date, vigente_hasta: date | None,
                      meses: int = 1, dias_gracia: int = 0) -> tuple[date, date]
    # Sin pago previo o vencida fuera de gracia: desde = hoy.
    # Vigente o dentro de gracia: desde = vigente_hasta + 1 día.
    # hasta = sumar_meses(desde, meses) - 1 día.

def estado_vigencia(*, hoy: date, vigente_hasta: date | None,
                    aviso_dias: int = 5) -> EstadoVigencia

def cubre(*, fecha: date, vigente_hasta: date | None) -> bool
    # Frontera inclusiva: el último día pagado está cubierto completo.

def monto_del_periodo(precio_mensual: Decimal, meses: int) -> Decimal
```

En `domain/tarifas.py` añada `cotizacion_pension(minutos) -> Cotizacion`:
importe cero, `es_cortesia=False` y desglose con regla `"pension"`. Así
`registrar_salida` conserva su firma `(Folio, Cotizacion)`.

En `domain/exceptions.py`: `PensionYaExiste`, `PensionNoEncontrada`,
`PensionDeBaja`, `PlanNoConfigurado`, `PagoNoCancelable`.

### 6.4 Casos de uso (`services_pensiones.py`)

Todos `@transaction.atomic`, argumentos solo por nombre, bitácora con
`pension=<id> placa=<placa>` y **sin nombre ni teléfono**.

| Función | Reglas |
|---|---|
| `registrar_pension(*, placa, tipo_vehiculo, nombre_cliente, telefono, descripcion_vehiculo, operador, notas="") -> Pension` | Normaliza placa. Rechaza si hay pensión activa con esa placa (`PensionYaExiste`); captura también el `IntegrityError` de la restricción. Asigna el plan activo del tipo (`PlanNoConfigurado` si no hay). No cobra |
| `cotizar_mensualidad(pension, *, meses=1, hoy=None) -> dict` | Solo lectura: periodo, plan, monto. Lo usa la pantalla de confirmación |
| `cobrar_mensualidad(*, pension_id, meses, pago_recibido, operador, momento=None) -> PagoPension` | `select_for_update` sobre la pensión. Exige turno abierto (`SinTurnoAbierto`) y respeta `exige_cierre_antes_de_cobrar`. Rechaza pensión de baja. Calcula periodo con el dominio, valida `pago_recibido ≥ monto` (`PagoInsuficiente`), crea el pago con copias del plan, actualiza `vigente_hasta` |
| `cancelar_pago(*, pago_id, motivo, operador) -> PagoPension` | Solo si el turno del pago **sigue abierto** y es el último pago vigente de la pensión; si no, `PagoNoCancelable` (la corrección posterior al corte va por `AjusteCorte`). Recalcula `vigente_hasta` desde los pagos vigentes restantes. Nivel `WARNING` |
| `dar_de_baja(*, pension_id, motivo, operador) -> Pension` | Motivo obligatorio. No borra ni reembolsa. Libera la placa para una alta futura |
| `pension_activa_de(placa) -> Pension \| None` | Consulta central, por placa normalizada |
| `listar(*, texto="", estado=None, hoy=None)` | `texto` busca con `icontains` en placa (normalizada), nombre y teléfono. Devuelve filas con `EstadoVigencia` ya resuelto |
| `cambiar_precio_plan(...)` | Desactiva y crea, igual que `_poner_precio_en_vigencia` |

### 6.5 Integración con la entrada y la salida (`services.py`)

**`registrar_entrada`** — tras normalizar la placa y antes de crear el folio:

```
pension = pension_activa_de(placa)
si pension y cubre(fecha local de `momento`, pension.vigente_hasta):
    folio.pension = pension
    tipo de vehículo = pension.tipo_vehiculo   # ignora lo elegido en pantalla
si no: folio de rotación, sin cambios
```

El folio conserva su `tarifa` (la FK es obligatoria y documenta el precio
exhibido ese día). El cajón se asigna igual que a cualquiera.

**`registrar_salida`** — al inicio, con el folio ya bloqueado:

```
si folio.pension_id:
    cotizacion = cotizacion_pension(minutos)
    importe = 0; pago_recibido = None; cambio = None
    turno = None            # no mueve efectivo: no pertenece a ningún corte
    no se exige turno ni se aplica exige_cierre_antes_de_cobrar
    no se copian los parámetros de tarifa
```

**`cotizar_folio`** devuelve `cotizacion_pension` para estos folios.
**`folios_en_patio`** añade a cada fila `es_pension` y `vigencia`
(`EstadoVigencia` o `None`); use `select_related("pension")`.

Importante: `registrar_salida` hoy adscribe el folio al turno del operador de
forma automática. Para folios de pensión esa adscripción **no debe ocurrir**;
de lo contrario cada salida de pensionado contaría como cortesía en el corte.

### 6.6 Integración con el corte

- `domain/corte.py`: `calcular_corte` recibe `importes_pension: Sequence[Decimal] = ()`.
  `ResumenCorte` gana `total_pensiones: Decimal = Decimal("0.00")` y
  `pagos_pension: int = 0` (con valor por omisión: los resúmenes JSON ya
  guardados y las pruebas existentes siguen siendo válidos).
  `efectivo_esperado = fondo_inicial + total_cobrado + total_pensiones`.
  `total_cobrado` **no cambia de significado**: sigue siendo boletos.
- `services_caja.py`: `_importes_pension_del_turno(turno)` (pagos `VIGENTE`);
  `calcular_resumen` y `cerrar_turno` lo pasan y graban los dos campos nuevos
  en `Turno`. La rama de turno cerrado lee `turno.total_pensiones or 0`.
- `resumen_operativo` añade `pagos_pension` (conteo, **sin importe**).
- Comprobante de corte (`corte.html`, texto plano y PDF si aplica): renglón
  «Pensiones cobradas (n)» entre «Total cobrado» y «Efectivo esperado», y tabla
  «Mensualidades cobradas en el turno» (placa, periodo, importe).
- `AjusteCorte` no cambia: parte de `efectivo_esperado`, que ya incluye pensiones.

### 6.7 Reportes y métricas

Los folios de pensión tienen `importe = 0` y hoy caerían como **cortesías**.
En cada agregación que cuenta cortesías con `Q(importe=CERO)` añada
`pension__isnull=True` (`services_metricas.py` ≈ líneas 204, 369 y 375;
`services_caja.py` ≈ 107 y 430). Además:

- **Clientes frecuentes** excluye placas con pensión activa (la métrica existe
  para decidir a quién ofrecerla).
- Horas pico y ocupación **sí** cuentan las entradas de pensionados: ocupan sitio.
- `reporte_por_rango` y `metricas_por_rango` añaden un bloque `pensiones`:
  ingresos por mensualidades del periodo (por `creado_en`), número de pagos,
  pensiones activas, por vencer y vencidas. Se muestra en `reportes.html`,
  `metricas.html` y en el PDF como una tabla más; **sin gráfica nueva**.
- La exportación CSV añade la columna `pension` (sí/no).

### 6.8 Boleto público y API

- `apps/boletos`: si el folio tiene pensión, la página muestra «Vehículo en
  pensión» en lugar del importe acumulado y **no** calcula tarifa por hora. No
  se exponen nombre, teléfono ni fecha de vencimiento. Añada el caso a
  `test_publico.py`.
- API REST: el serializador de folio expone `es_pension` (solo lectura). Sin
  rutas nuevas.

---

## 7. Pantallas

### 7.1 Caja — captura (`caja.html`)

Orden vertical en teléfono (en escritorio, captura a la izquierda y patio a la
derecha, rejilla `minmax(320px, 420px) 1fr`):

1. Aviso de turno (sin cambio de reglas).
2. **Tarjeta de captura** — un solo formulario:
   - Campo `placa` grande (`data-mayusculas`, `data-filtra="#patio"`, `autofocus`).
   - `.segmentado` «Automóvil | Motocicleta | Accesible» (por omisión
     Automóvil; conserva los valores de `TipoCajon`).
   - `.acciones-dobles`: **Entrada** (`name="accion" value="entrada"`) y
     **Cobrar salida** (`value="cotizar"`). `Enter` dispara Entrada (primer
     botón del formulario), como hoy.
3. Indicadores de ocupación (si hay inventario), en una sola fila compacta.
4. **Patio** (`id="patio"`): contador «Vehículos dentro (41)», filas con
   `data-placa`, píldora `PENSIÓN` / `PENSIÓN VENCIDA` cuando aplique, y
   acciones «Cobrar» (rotación) o «Salida» (pensión) + «Boleto».

Resolución en el servidor (una vista, un formulario `CapturaForm` que sustituye
a `EntradaForm` + `BuscarPlacaForm`; conserve las claves POST `accion`,
`placa`, `tipo_cajon` para no romper pruebas):

| Acción | Estado de la placa | Respuesta |
|---|---|---|
| Entrada | No está dentro, sin pensión | Folio → redirección al boleto QR (sin cambio) |
| Entrada | Pensión vigente | Folio de pensión → **vuelve a la caja** con aviso «Entrada de pensionado ABC123 · vigente hasta 14/12». Sin QR |
| Entrada | Pensión vencida | Folio de rotación → boleto QR, con aviso `warning` «Pensión vencida el 02/11: se cobra por hora» y enlace «Renovar» |
| Entrada | Ya está dentro | No es error: muestra la tarjeta de cobro de ese folio con aviso «ABC123 ya estaba dentro desde las 10:32» |
| Cobrar salida | Dentro, rotación | Tarjeta de cobro (§7.2) |
| Cobrar salida | Dentro, pensión | Tarjeta de salida de pensión (§7.3) |
| Cobrar salida | No está dentro | Aviso con las placas del patio que **contienen** lo tecleado (hasta 5, como botones) y la opción «Registrar entrada de ABC123» |

Los errores de validación se muestran **junto al campo** y el formulario
conserva lo tecleado (se re-renderiza, no se redirige).

### 7.2 Caja — cobro

Tarjeta `.destacada` sobre la captura:

- Placa, entrada, permanencia, tarifa, reglas aplicadas (como hoy).
- `.cifra` con el importe.
- `.atajos`: **Exacto**, $50, $100, $200, $500 (solo los ≥ importe; lista en
  `settings.IRCARS_ATAJOS_DE_PAGO`).
- Campo «Pago recibido» (`data-cambio`) y línea de estimación del cambio.
- Botón primario «Cobrar y cerrar folio»; enlace discreto «Cancelar folio por
  error de captura»; botón fantasma «Volver sin cobrar».

Tras cobrar: en lugar de solo un mensaje, la caja muestra una
`.tarjeta.resultado` con «Cambio a entregar» y la cifra en `.cifra.cambio`
(o «Cobro exacto»), y el botón «Siguiente vehículo» con `autofocus`.
Implementación: el redireccionamiento tras POST se conserva; los datos del
resultado viajan en la sesión con lectura única (`request.session.pop`), no en
la URL. El mensaje flash actual se mantiene como respaldo accesible.

### 7.3 Caja — salida de pensionado

Tarjeta con filete `--pension`: placa, píldora de vigencia, permanencia,
«Sin cobro: estancia cubierta por pensión» y botón «Registrar salida»
(`accion=cobrar`, sin campo de pago). Si la vigencia es `VENCIDA` o
`POR_VENCER`, aviso con botón «Cobrar mensualidad» a la ficha (D7). No exige
turno abierto.

### 7.4 Pensiones — lista (`pensiones/lista.html`)

- Encabezado con botón primario «Nueva pensión».
- Indicadores: Activas · Por vencer · Vencidas (conteos, sin importes).
- `.segmentado` de filtro por estado (enlaces GET) y campo de búsqueda
  (GET `?q=`, resuelto en el servidor con `icontains`; `data-filtra` como
  mejora).
- `.lista`: placa (celda principal), nombre, vehículo, «Vigente hasta 14/12» +
  píldora, acción «Cobrar» / «Ver». Orden: vencidas, por vencer, vigentes.
- Paginación del servidor a 50.

### 7.5 Pensiones — alta (`pensiones/alta.html`)

Un formulario, una columna: placa → tipo de vehículo (`.segmentado`; al
elegirlo se muestra el plan y su precio mensual) → nombre → teléfono
(`inputmode="tel"`) → descripción del vehículo → notas. Pie con el aviso de
privacidad. Botones: «Guardar y cobrar primer mes» (lleva a `/cobrar/`) y
«Guardar sin cobrar». Si la placa tiene un folio de rotación abierto, se avisa:
la estancia en curso se cobra por hora; la pensión rige desde la siguiente
entrada.

### 7.6 Pensiones — ficha, cobro y recibo

- **Ficha:** datos, píldora de vigencia en grande, botón primario «Cobrar
  mensualidad», historial de pagos (periodo, importe, operador, turno, estado),
  últimas entradas/salidas. Acciones administrativas plegadas: editar datos de
  contacto, dar de baja (motivo + contraseña), cancelar último pago.
- **Cobro (dos pasos, como la caja):** GET muestra periodo calculado
  («15/11/2026 – 14/12/2026»), `.segmentado` de meses (1 · 2 · 3 · 6 · 12),
  plan y `.cifra` con el monto; atajos y «Pago recibido». POST cobra y redirige
  al recibo. Sin turno abierto, la pantalla lo dice arriba y deshabilita el
  cobro con enlace a «Abrir turno».
- **Recibo:** imprimible (`@media print` ya oculta navegación): marca, folio
  del recibo (UUID corto), placa, periodo, plan, importe, pago y cambio, fecha,
  operador y la leyenda «Precio vigente del plan al momento del pago».

### 7.7 «Más», acceso y resto

- `mas.html`: `.menu` con las secciones según perfil, «Mi contraseña» y «Salir»
  (formulario POST, se conserva la protección CSRF).
- `acceso.html`: fondo `--marca`, logotipo centrado, tarjeta sobre el fondo.
- Turno, corte, reportes, métricas, operadores, precio: **solo** adoptan
  tokens, utilidades de espaciado y componentes nuevos. Ningún cambio de flujo.

---

## 8. Plan de ejecución

Un commit por fase. No se empieza una fase con la anterior en rojo.

| Fase | Contenido | Archivos principales | Criterio de aceptación |
|---|---|---|---|
| **0** | Línea base | — | `pytest` en verde; anote el número de pruebas |
| **1** | Tokens, escalas, componentes nuevos, utilidades; retirar estilos en línea; `theme-color`; pantalla de acceso | `estilos.css`, `base.html`, `acceso.html`, manifiesto en `web/views.py` | Ninguna prueba cambia de resultado. Claro y oscuro revisados a 360 px y 1280 px. Cero `style="` con color; cero `style="margin` |
| **2** | Navegación: cuatro destinos, página «Más», `aria-current`, enlace de salto | `base.html`, `mas.html`, `web/urls.py`, `web/views.py`, `test_apariencia.py` | Todas las secciones alcanzables en teléfono. Prueba nueva: «Más» lista Métricas solo a administrativos |
| **3** | Caja: captura única, segmentado, tarjeta de cobro con atajos, hoja de resultado, filtro del patio, `caja.js`, errores junto al campo | `caja.html`, `web/forms.py`, `web/views.py`, `caja.js`, `test_web_*.py` | Los flujos de `test_web_boleto`, `test_web_turno`, `test_caja` pasan. Pruebas nuevas para cada fila de la tabla de §7.1 (sin pensión). Operable con JavaScript desactivado |
| **4** | Pensiones: dominio, modelos, migración, servicios, integración con entrada/salida | `domain/pensiones.py`, `domain/tarifas.py`, `domain/exceptions.py`, `models.py`, `0008_pensiones.py`, `services_pensiones.py`, `services.py` | Pruebas de §9.1 y §9.2 en verde. `makemigrations --check` limpio. Migración reversible y sin datos |
| **5** | Pensiones en el corte, reportes, métricas, PDF, boleto público, API | `domain/corte.py`, `services_caja.py`, `services_metricas.py`, `reportes_pdf.py`, `corte.html`, `apps/boletos/*`, `serializers.py` | Pruebas de §9.3. Las pruebas de corte existentes pasan **sin modificarse** |
| **6** | Pensiones: pantallas y su presencia en la caja | `web/views_pensiones.py`, `web/urls.py`, `web/forms.py`, `pensiones/*.html`, `caja.html`, `admin.py` (solo lectura) | Pruebas de §9.4. Recorrido manual: alta → cobro → entrada → salida → corte cuadra |
| **7** | Documentación y siembra | `README.md`, `CASETA.md`, `.env.example`, `sembrar_demo` (un plan por tipo de vehículo) | README describe pensiones y las retira de «Pendientes»; `CASETA.md` añade el ciclo del pensionado; número de casos actualizado |

Orden deliberado: el dominio y el corte (4–5) van antes que las pantallas (6)
para que ninguna pantalla pueda cobrar una mensualidad que el corte todavía no
sabe sumar.

---

## 9. Pruebas mínimas exigidas

### 9.1 Dominio (`test_pensiones_dominio.py`, sin base de datos)
- `sumar_meses`: 31 ene → 28 feb (29 en bisiesto); 15 dic → 15 ene del año siguiente.
- `periodo_siguiente`: sin pago previo; vigente (continúa sin hueco ni traslape);
  vencida dentro y fuera de gracia; varios meses.
- `cubre`: último día cubierto completo; día siguiente no.
- `estado_vigencia`: cada frontera de `aviso_dias`.
- Dos renovaciones seguidas no dejan días sin cubrir ni cubiertos dos veces.

### 9.2 Servicios (`test_pensiones_servicios.py`)
- Alta normaliza la placa; segunda alta activa de la misma placa falla (servicio
  **y** restricción de base); tras la baja, la placa puede volver a darse de alta.
- Cobro sin turno abierto → `SinTurnoAbierto`; con turno de otro día → frenado.
- Pago insuficiente no crea nada; el pago copia nombre y precio del plan.
- Cambiar el precio del plan no altera pagos anteriores ni `vigente_hasta`.
- Entrada con pensión vigente → folio con `pension`; vencida → folio de rotación.
- La fecha de vigencia se evalúa en **zona local** (entrada a las 23:30 del
  último día cubierto sigue cubierta; a las 00:10 del siguiente, no).
- Salida de folio de pensión: importe 0, sin turno, sin exigir turno abierto,
  y con turno abierto del operador **tampoco** se adscribe.
- Cancelar pago: solo con su turno abierto; recalcula `vigente_hasta`.
- La bitácora no contiene nombre ni teléfono (`caplog`).

### 9.3 Corte y reportes (`test_pensiones_corte.py`)
- `calcular_corte` sin `importes_pension` da el mismo resultado que hoy.
- Efectivo esperado = fondo + boletos + mensualidades; un pago cancelado no suma.
- Las salidas de pensionados **no** incrementan `folios_cortesia` del corte ni
  de las métricas.
- La fotografía del turno cerrado conserva `total_pensiones` aunque después se
  cobre otra mensualidad en otro turno.
- `resumen_operativo` no expone importes de pensiones.
- Clientes frecuentes excluye placas con pensión activa.
- El boleto público de un folio de pensión no muestra importe ni datos personales.

### 9.4 Web (`test_pensiones_web.py`)
- Todas las rutas exigen sesión; planes, baja y cancelación exigen perfil
  administrativo (y contraseña donde se indica).
- Búsqueda insensible a mayúsculas por placa, nombre y teléfono.
- Alta + cobro + recibo de extremo a extremo; doble envío del cobro no cobra
  dos veces (redirección tras POST + bloqueo de fila).
- La caja marca al pensionado en el patio y ofrece «Salida» sin campo de pago.
- Ninguna plantilla nueva fija colores en línea (la prueba existente ya recorre
  el directorio).

---

## 10. Riesgos y cómo quedan cubiertos

| Riesgo | Mitigación |
|---|---|
| Mensualidad cobrada fuera del corte | `PagoPension.turno` no nulo; el servicio exige turno abierto |
| Operador regala estancias marcándolas como pensión | La pensión no se elige en pantalla: la decide la placa contra la base. Alta y pagos quedan atribuidos a un operador |
| Salidas de pensión inflan las cortesías y ocultan cortesías reales | Folio de pensión sin turno + filtro `pension__isnull=True` + prueba dedicada |
| Doble alta simultánea de la misma placa | Restricción única parcial en la base |
| Doble cobro por doble toque | `select_for_update` + redirección tras POST |
| Cambio de precio aplicado a periodos pagados | Precio copiado al pago; planes inmutables |
| Fuga de datos personales | No van al boleto público, ni a la bitácora, ni a la API |
| El rediseño rompe la operación sin JavaScript o en un teléfono viejo | Mejora progresiva; sin sintaxis que exija un navegador reciente en `caja.js` |
| Vencimiento calculado en UTC | Dominio recibe `date` local; prueba de frontera de medianoche |

Observabilidad: bitácora `INFO` en alta, cobro, entrada y salida de pensión;
`WARNING` en cancelación de pago y baja. Sin métricas ni sondas nuevas.

---

## 11. Definición de terminado

- Las ocho fases están en commits separados y la suite completa pasa en SQLite
  y en PostgreSQL.
- `makemigrations --check --dry-run` y `manage.py check` limpios.
- Recorrido manual en 360 px (claro y oscuro) y 1280 px: turno → entrada →
  cobro con atajo → alta de pensión → cobro de mensualidad → entrada y salida
  del pensionado → corte que cuadra incluyendo la mensualidad.
- La pantalla coincide con `docs/rediseno/maqueta.html` en estructura,
  jerarquía y componentes.
- `README.md` y `CASETA.md` describen lo construido.

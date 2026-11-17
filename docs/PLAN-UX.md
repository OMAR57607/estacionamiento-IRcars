# Mejora de UX: conteos, reportes y turno — plan de ejecución

Continuación del commit «Presenta el corte como una cuenta y unifica los
conteos». Ese commit dejó coherentes **turno, corte y corrección**. Este plan
cubre lo que quedó fuera. Está escrito para ejecutarse **una fase por sesión,
un commit por fase, con la suite en verde al cerrar cada una**.

Rigen todas las reglas de [`docs/REDISENO.md`](REDISENO.md) §0. Las que más se
van a tocar aquí:

- Ninguna regla de negocio en vistas ni plantillas: `web/` → `services*.py` → `domain/`.
- Los comentarios existentes no se borran; si la decisión cambia, se actualizan.
- El cierre ciego no se debilita: el cajero nunca ve importes del turno abierto.
- Una prueba que falle por un cambio deliberado se actualiza con su docstring; no se omite.
- Sin dependencias nuevas. Todo funciona sin JavaScript.

## 0. Preparación (en cada sesión)

```bash
pip install -r requirements.txt
export DJANGO_SECRET_KEY=x DJANGO_DEBUG=False \
       DJANGO_ALLOWED_HOSTS=testserver,localhost \
       DATABASE_URL=sqlite:///pruebas-local.sqlite3
pytest -q -p no:warnings          # debe pasar ANTES de tocar nada
```

Al cerrar cada fase: `python manage.py makemigrations --check --dry-run`,
`python manage.py check`, `pytest`. Ninguna fase de este plan necesita
migración; si `makemigrations` detecta cambios, algo se tocó de más.

Para ver una pantalla: `python manage.py migrate && python manage.py sembrar_demo`
y `runserver` con `DJANGO_DEBUG=True IRCARS_TRAS_PROXY=False`. Con `--noreload`
las plantillas quedan en caché: reinicie el servidor tras editarlas. Revise
siempre a 390 px de ancho además de escritorio.

## 1. Definiciones que no se negocian

Son las que ya aplican turno y corte. Toda pantalla, reporte y PDF debe usar
estas palabras con este significado, y solo este:

| Término en pantalla | Significado | Dónde se calcula hoy |
|---|---|---|
| **Boletos cobrados** | Salidas con importe mayor que cero | `domain/corte.py::calcular_corte`, `services_caja.resumen_operativo` |
| **Sin costo (cortesía)** | Salidas con importe cero que **no** son de pensionado | ídem |
| **Salidas** | Todas las salidas registradas: cobradas + sin costo (+ pensionado cubierto, donde aplique) | — |
| **Pensiones cobradas** | Pagos de pensión vigentes (no cancelados) | `services_caja._importes_pension_del_turno` |
| **Debería haber en caja** | Cambio + boletos cobrados + pensiones cobradas | `calcular_corte` |
| **Diferencia** | Contado − debería haber; la **vigente** si hubo corrección | `Turno.diferencia_vigente` |

En la caseta se dice **boleto** y **cambio**, nunca «folio» ni «fondo inicial»
(lo fija `test_web_turno.py::TestVocabulario`). «Folio» sigue siendo válido en
el código, en la API y en los nombres de claves.

---

## Fase 1 — Reportes y métricas cuentan igual que el corte

**Problema verificado.** `services_caja.reporte_por_rango` devuelve en
`folios_cobrados` **todas** las salidas cerradas, cortesías incluidas. Con los
datos de ejemplo, Reportes muestra «Folios cobrados 5 · Cortesías 1» y el
corte del mismo turno «Boletos cobrados 4 · Sin costo 1». `importe_promedio`
divide entre las 5, así que una cortesía baja el promedio. Métricas muestra el
mismo 5 bajo «Boletos cobrados».

**Cambios.**

1. `services_caja.py::reporte_por_rango`:
   - `folios_cobrados` → solo `importe__gt=0`.
   - clave nueva `salidas` → el total que hoy devuelve `folios_cobrados`.
   - `importe_promedio` → total entre boletos **cobrados**.
   - `minutos_promedio` → sigue sobre todas las salidas (la permanencia no depende de si se pagó).
2. `services_metricas.py`: lea la función que arma el resumen (la que alimenta
   «Boletos cobrados» en `metricas.html` línea ~61 y `reportes_pdf.py` línea
   ~444) y aplique la misma definición. La composición «Con cobro / Cortesía /
   Pensión» ya separa bien: no la cambie, úsela como referencia.
3. `reportes.html`: «Folios cobrados» → «Boletos cobrados» (indicador);
   añadir indicador «Salidas»; título del listado «Folios cobrados» →
   «Salidas del periodo»; texto vacío en consecuencia; en la columna Importe,
   «Sin costo» cuando el importe es cero (como ya hace `corte.html`).
4. `reportes_pdf.py`: mismos rótulos y misma cifra que la pantalla.

**Pruebas.** Una en `test_caja.py` (o donde ya se pruebe `reporte_por_rango`):
con un cobro de $30 y una cortesía, `folios_cobrados == 1`, `salidas == 2`,
`folios_cortesia == 1`, `importe_promedio == 30.00`. Otra que compare el
reporte del periodo con el corte del turno que lo cubre: mismos conteos.
Actualice las de `test_metricas.py` y `test_reportes_pdf.py` que fijen la
cifra anterior, con su docstring.

**Hecho cuando:** corte, Reportes, Métricas y PDF dan los mismos «Boletos
cobrados» y «Sin costo» para el mismo turno.

## Fase 2 — Una sola palabra para el boleto

**Problema verificado.** Textos visibles que aún dicen «folio»:

- `caja.html` líneas ~149 («Cobrar y cerrar folio»), ~155 («Cancelar este folio…»), ~218 («…el folio se cierra después…»).
- `cancelar.html` líneas ~7, ~12, ~28.
- `operador.html` líneas ~28 y ~80; `operadores.html` línea ~48.
- Los de `reportes.html` se resuelven en la fase 1.

**Cambios.** Sustituir por «boleto» en el texto visible, sin tocar nombres de
variables, `name=` de formularios ni URLs. Revise también los mensajes de
`web/views.py` y los de `domain/exceptions.py` que llegan al usuario por
`messages.error(request, str(exc))`: si alguno dice «folio» y se muestra en
pantalla, cámbielo; si es solo bitácora (`logger`), déjelo.

**Pruebas.** Amplíe `TestVocabulario` con una prueba parametrizada sobre caja,
cancelación y operadores: «Folio»/«folio» no aparece en el HTML fuera de
atributos. Busque antes con `grep -rn "folio" apps/*/tests` qué aserciones
dependen de los textos actuales.

**Hecho cuando:** `grep -rni "folio" apps/estacionamiento/templates` solo
devuelve variables, etiquetas de plantilla y comentarios.

## Fase 3 — El dueño ve los turnos que están abiertos

**Problema verificado.** `web/views.py::turno` solo muestra el turno abierto
**del propio usuario** (`_contexto_base` → `obtener_turno_abierto(request.user)`).
El dueño entra a «Turno y corte», ve «Abrir turno de caja» y el histórico,
pero no el turno que su cajero tiene abierto ni cuánto debería haber en él. El
comentario de la vista dice que al dueño no se le oculta esa cifra; en la
práctica solo la ve si el turno es suyo.

**Cambios.**

1. `services_caja.py`: función nueva `turnos_abiertos()` → turnos en estado
   abierto con `select_related("operador")`, ordenados por `abierto_en`.
2. `web/views.py::turno`: solo si `es_administrativo`, pasar al contexto una
   lista con, por cada turno abierto **ajeno**, el turno, su
   `resumen_operativo`, su `calcular_resumen` y su `estado_del_turno`.
3. `turno.html`: tarjeta «Turnos abiertos ahora», antes del histórico, con una
   entrada por turno: operador, desde cuándo, aviso si `es_largo`, y la tabla
   `.cuenta` (cambio + boletos + pensiones = debería haber). Reutilice el
   marcado de la cuenta que ya está en esa plantilla; si queda duplicado,
   extráigalo a un `{% include %}`.
4. **No** añada aquí el botón de cerrar un turno ajeno: quién puede cortar la
   caja de otro es una decisión de control que no está tomada.

**Pruebas** en `test_web_turno.py`: el perfil administrativo ve el esperado
del turno de otro operador; el cajero **no** ve ni la tarjeta ni ningún importe
de turnos ajenos (misma forma que `test_el_cajero_no_ve_el_esperado`).

**Hecho cuando:** con un cajero con turno abierto, el dueño ve en `/turno/`
cuánto debería haber en esa caja, y el cajero sigue sin ver ningún importe.

## Fase 4 — Tablas que se leen en el teléfono

**Problema verificado** (capturas a 390 px):

- Histórico «Turnos cortados»: 9 columnas; a partir de «Operador» hay que desplazar.
- Corte → «Correcciones aplicadas»: la columna Motivo se parte a una palabra por línea.
- Corte → «Salidas del turno» y Reportes → listado: la columna Importe queda fuera de la pantalla.

**Cambios.** El repositorio ya tiene el patrón: `.lista-fila` con
`grid-template-areas`, que se apila bajo 680 px sin duplicar marcado
(`estilos.css` ~660; úselo como en `operadores.html` y `pensiones/lista.html`).
Aplíquelo a esas cuatro listas. En el teléfono cada fila debe mostrar sin
desplazar: lo principal (fecha o placa), el resultado o importe, y debajo el
resto en una línea. En escritorio y **al imprimir** el corte debe seguir
viéndose como tabla alineada: revise las reglas `@media print`.

**Pruebas.** `test_apariencia.py` y `test_plantillas.py` fijan parte del
marcado: léalos antes. No se piden pruebas nuevas de CSS; sí que las
existentes sigan pasando y una captura a 390 px de cada lista en la
descripción del commit o del PR.

**Hecho cuando:** a 390 px ninguna de las cuatro listas tiene desplazamiento
horizontal y el comprobante impreso no cambió de forma.

## Fase 5 — Confirmar antes de cerrar *(requiere visto bueno del dueño)*

**Motivo.** El cierre es definitivo y un error de dedo en el efectivo (9825
por 982.50) produce un faltante falso que solo se arregla con una corrección
administrativa. Es justo el caso que originó `AjusteCorte`.

**Propuesta.** Paso intermedio en servidor: al enviar el conteo, en lugar de
cerrar, mostrar «Va a cerrar el turno declarando **$982.50**. Esta cifra no
se puede cambiar después.» con «Confirmar y cerrar» y «Corregir la cifra». La
pantalla de confirmación **no** muestra el esperado ni la diferencia: el
cierre sigue siendo ciego. De paso, acortar la etiqueta del campo a «Efectivo
contado» y bajar el resto a texto de ayuda.

**No la ejecute sin confirmación:** añade un toque a una operación diaria y
cambia el flujo que documenta `CASETA.md`. Si se aprueba: acción nueva
`confirmar` en `views.turno`, pruebas de que un solo envío no cierra el turno
y de que la confirmación no revela importes, y actualización de `CASETA.md`.

## Fase 6 — Documentación

Añadir a `README.md` una sección «Cómo se lee el corte» con la tabla de §1 y
la cuenta en tres renglones, y repasar `CASETA.md` para que los rótulos que
cita coincidan con los de pantalla tras las fases 1 a 4.

---

## Orden y dependencias

1 → 2 → 3 → 4 → 6. La 5 es independiente y va cuando se apruebe. La 4 toca
las mismas plantillas que la 1 y la 3: no la adelante.

## Cuándo detenerse y preguntar

- Una prueba existente contradice una definición de §1 y su docstring explica por qué.
- Un cambio exigiría migración, tocar la API REST o el boleto público (`apps/boletos`).
- Cualquier cosa que haga visible al cajero un importe del turno abierto.

## Instrucción de arranque para cada sesión

> Ejecuta la **Fase N** de `docs/PLAN-UX.md` y solo esa. Lee antes
> `docs/REDISENO.md` §0 y los archivos que la fase nombra. Corre la suite
> antes de empezar y al terminar. Un commit, en la rama de trabajo, sin pull
> request. Al final dime qué cambió, qué pruebas añadiste o actualizaste y
> cualquier cosa del plan que no coincidiera con el código.

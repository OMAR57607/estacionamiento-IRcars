# Despliegue en Railway con Supabase

Configuración del servicio desplegado, la decisión de base de datos que lo
sustenta y lo que falta por hacer.

---

## Estado de la configuración

| Elemento | Valor | Estado |
|---|---|---|
| Proyecto Railway | `kind-intuition` | configurado |
| Servicio | `estacionamiento-IRcars` | configurado |
| Repositorio | `OMAR57607/estacionamiento-IRcars`, rama `claude/ecstatic-hamilton-ytihjo` | conectado |
| Dominio | `estacionamiento-ircars-production.up.railway.app` | asignado |
| Constructor | Railpack (detecta Django por `requirements.txt` y `Procfile`) | configurado |
| Sonda de salud | `/healthz`, 60 s de margen | configurada |
| Política de reinicio | `ON_FAILURE`, hasta 5 intentos | configurada |
| Base de datos | Supabase PostgreSQL 17, esquema `estacionamiento` | esquema creado |
| `DATABASE_URL` | — | **pendiente: la fija el responsable** |

---

## Por qué Supabase y no una base local

La premisa inicial era alojar la base de datos localmente. Conviene dejar
escrito por qué se descartó, para que la decisión no se revierta por olvido.

**Una base local no aporta resiliencia si la aplicación vive en la nube.** El
único beneficio real de SQLite en el propio contenedor sería seguir cobrando sin
internet. Pero si el servicio está en Railway, una falla de red deja al operador
sin acceso de todas formas. SQLite habría impuesto todas las limitaciones de una
base local sin ninguno de sus beneficios.

A eso se suman tres consecuencias técnicas concretas:

1. **El disco del contenedor es efímero.** Sin un volumen montado, el archivo
   desaparece en cada despliegue. Para datos de cobro, pérdida total sin aviso.
2. **`select_for_update` no funciona en SQLite.** Es un no-op silencioso. Las
   protecciones contra doble cobro y doble ocupación quedarían reducidas a los
   índices únicos. En un sistema que maneja dinero, no es un detalle menor.
3. **Una sola réplica, para siempre.** SQLite es un archivo: dos contenedores
   escribiéndolo lo corrompen.

Con PostgreSQL los bloqueos de fila sí operan, hay respaldos automáticos y el
ciclo de vida de los datos es independiente del despliegue. El código ya estaba
validado contra PostgreSQL: las 91 pruebas se ejecutan en ambos motores.

El ejecutable local de escritorio (`main.py`, carpeta `escritorio/`) sigue
existiendo y **ahí sí** usa SQLite local, que es su contexto correcto: un solo
equipo, sin red, en la caseta.

---

## Aislamiento por esquema

El proyecto Supabase `OMAR57607's Project` ya aloja una aplicación Django en el
esquema `public`: 65 tablas, entre ellas `catalogo_toyota` con 846 mil
registros, `django_migrations` con 158 migraciones y `auth_user` con 23
usuarios.

**Mezclar el estacionamiento en ese esquema habría sido un error grave.** Dos
bases de código Django compartiendo `django_migrations` y las tablas `auth_*`
es una fuente conocida de corrupción: cada despliegue ejecuta su propio
`migrate`, y uno puede intentar alterar lo que el otro creó.

La solución es un esquema dedicado. Con `search_path` apuntando a
`estacionamiento`, **todas** las tablas de este sistema —incluidas
`django_migrations`, `auth_user` y `django_session`— se crean ahí. El esquema
`public` queda intacto.

### La regla que no debe romperse

El `search_path` contiene **exclusivamente** `estacionamiento`. **Nunca debe
añadirse `public`.**

Si `public` estuviera en la ruta de búsqueda, el `migrate` de este sistema
leería el `django_migrations` del sistema Toyota, concluiría que las migraciones
de `auth` y `contenttypes` ya están aplicadas, y omitiría crear esas tablas en
nuestro esquema. El resultado sería un esquema incompleto que falla en la
primera consulta, con un error que no apunta a su causa.

`pg_catalog` se resuelve siempre de forma implícita, así que las funciones y
tipos del sistema siguen disponibles sin incluir `public`.

### Ventaja de seguridad adicional

PostgREST —la API automática de Supabase— solo expone los esquemas
configurados, que por omisión son `public` y `graphql_public`. Al no estar
expuesto, el esquema `estacionamiento` **no es alcanzable** por la API pública
ni por las claves anónimas. El único acceso es la conexión directa de
PostgreSQL que usa el servicio.

---

## Variables de entorno

Ya configuradas en Railway:

| Variable | Valor | Propósito |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(generada, 80 caracteres)* | Clave criptográfica propia del entorno |
| `DJANGO_DEBUG` | `False` | En `True` expondría trazas completas |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | El dominio de Railway se añade solo |
| `DJANGO_TIME_ZONE` | `America/Mexico_City` | Determina el corte de día del tope diario |
| `DJANGO_LOG_LEVEL` | `INFO` | |
| `DJANGO_DB_SCHEMA` | `estacionamiento` | Activa el aislamiento por esquema |

El dominio público se lee de `RAILWAY_PUBLIC_DOMAIN`, que Railway inyecta
automáticamente: no hay que actualizar ninguna lista al regenerarlo, olvido que
se manifestaría como un `400 Bad Request` sin explicación visible.

### Pendiente: `DATABASE_URL`

Es la única variable que falta, y contiene la contraseña de la base de datos.
**Debe fijarla el responsable**, no quedar registrada en una conversación ni en
el repositorio.

Formato, usando el *pooler* de Supabase:

```
postgresql://postgres.vbkoyhpiknfbaijbroan:<CONTRASEÑA>@aws-0-us-west-2.pooler.supabase.com:5432/postgres
```

Tome la cadena exacta del panel de Supabase, en **Project Settings → Database →
Connection string**, modo **Session pooler**. Conviene el *pooler* en modo
sesión y no la conexión directa por dos razones: la conexión directa requiere
el complemento de IPv4, y el modo sesión es compatible con las conexiones
persistentes de Django (`CONN_MAX_AGE`), mientras que el modo transacción
(puerto 6543) exigiría desactivarlas y deshabilitar los cursores de servidor.

Un dato favorable: el proyecto Supabase está en **us-west-2** y el servicio de
Railway en **us-west2**. Misma región, latencia mínima.

Al guardar esa variable, Railway redespliega y el `Procfile` ejecuta `migrate`,
que crea las 14 tablas dentro del esquema.

---

## Advertencia sobre el plan gratuito de Supabase

Los proyectos del plan gratuito **se pausan tras aproximadamente 7 días sin
actividad**. El proyecto `Nitro_Garage` de esta misma cuenta está precisamente
`INACTIVE`, lo que lo demuestra.

**Una base pausada es una caja detenida.** Un estacionamiento opera a diario,
así que en la práctica se mantendría activo, pero conviene conocer el riesgo
antes de que ocurra en un fin de semana largo.

---

## Primer arranque

Tras fijar `DATABASE_URL` y que el despliegue quede en verde:

```bash
# 1. Verificar que el servicio responde
curl https://estacionamiento-ircars-production.up.railway.app/healthz

# 2. Verificar que la base de datos responde
curl https://estacionamiento-ircars-production.up.railway.app/api/v1/salud/
```

La segunda sonda devuelve `503` si la base no responde. Es la que debe
vigilarse: un proceso vivo con base inaccesible no sirve para cobrar.

Después, desde la consola del servicio en Railway:

```bash
# Crear el primer usuario administrador
python manage.py createsuperuser

# Opcional: sembrar una tarifa y cajones de prueba
python manage.py sembrar_demo
```

Si no siembra cajones, el sistema opera en modo «solo tiempo y caja»: los
folios se emiten sin cajón y no hay control de capacidad. Es el comportamiento
previsto cuando no se administra inventario de espacios.

Luego entre a la interfaz de caja en la raíz del dominio, registre la tarifa
desde la pestaña **Tarifa** y abra turno antes de cobrar.

---

## Vigilancia

| Qué | Dónde |
|---|---|
| Proceso vivo | `GET /healthz` |
| Base de datos accesible | `GET /api/v1/salud/` — devuelve `503` si falla |
| Eventos de negocio | Logs del servicio en Railway: entrada, cobro, cancelación y corte, cada uno con su folio |
| Faltantes de caja | Los cierres con diferencia se registran en nivel `WARNING` |
| Avisos de la base | Panel de Supabase, sección *Advisors* |

---

## Cuándo reconsiderar la arquitectura

- **Varias casetas simultáneas**: ya está soportado. PostgreSQL aplica los
  bloqueos de fila y los índices únicos parciales impiden la doble ocupación.
- **Más de una réplica en Railway**: posible con PostgreSQL. Súbala solo si la
  carga lo justifica.
- **Operación sin internet**: no es resoluble en la nube. Para ese escenario
  está el ejecutable local de escritorio, que opera con SQLite en el equipo.

# Despliegue web (no activo)

> **Estado: retirado.** El sistema opera hoy con el **ejecutable de escritorio**
> (ver [README](README.md)). El servicio en Railway y su base de datos fueron
> eliminados. Este documento se conserva porque el código de la interfaz web y
> de la API REST sigue completo y funcional, y porque las trampas que
> encontramos volverían a aparecer si algún día se retoma.

---

## Por qué se retiró

El estacionamiento mueve 13–15 autos al día, unas 30 operaciones diarias, y la
base de datos debía vivir en el equipo de la caseta. Con esas dos condiciones,
un despliegue en la nube resultaba contraproducente:

- Si la aplicación corre en la nube y la base en el equipo local, la caja
  depende de **dos** cosas a la vez —el proveedor y la conexión del sitio— y
  obliga a exponer PostgreSQL a internet.
- Si todo corre en la nube, la caseta deja de cobrar cuando se cae la red.
- El ejecutable local no tiene ninguno de los dos problemas y, a ese volumen,
  SQLite sobra: unos 5,500 folios al año.

No fue un problema técnico del despliegue, sino un desajuste entre la
arquitectura y la operación real.

---

## Lo que el código conserva

Sigue presente y probado:

| Componente | Archivo |
|---|---|
| Interfaz web de caja | `apps/estacionamiento/web/`, `templates/`, `static/` |
| API REST | `apps/estacionamiento/views.py`, `serializers.py`, `urls.py` |
| Configuración de servidor | `config/settings.py` |
| Imagen de producción | `Dockerfile`, `scripts/arrancar.sh` |
| Entorno local con Docker | `docker-compose.yml` |

Las 91 pruebas se ejecutan tanto en SQLite como en PostgreSQL, así que el
camino de vuelta está abierto: bastaría crear el servicio, fijar las variables
y apuntar `DATABASE_URL` a una base PostgreSQL.

---

## Tres trampas que costaron despliegues fallidos

Si se retoma este camino, estas son las que volverían a morder.

### 1. El Dockerfile manda, el Procfile no

**Railway construye con el `Dockerfile` cuando existe e ignora el `Procfile`**,
aunque el constructor figure configurado como Railpack.

El `Dockerfile` se había escrito para Docker Compose en local, con
`CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]`. El resultado fue un
despliegue que habría servido producción con el **servidor de desarrollo de
Django**, en un puerto fijo distinto al que asigna la plataforma y sin aplicar
migraciones. El error visible era otro —faltaba `DATABASE_URL`— y tapó el
problema de fondo.

La corrección, que sigue vigente: **una sola fuente de verdad**, el script
`scripts/arrancar.sh`, invocado tanto por el `CMD` del `Dockerfile` como por el
`Procfile`. `docker-compose.yml` lo sobrescribe con `runserver` para el trabajo
local, que es donde sí corresponde.

### 2. Nunca compartir instancia con otra aplicación Django

Se intentó alojar las tablas en un proyecto Supabase que ya hospedaba otro
sistema Django en el esquema `public` —846 mil registros de catálogo, 158
migraciones aplicadas, 23 usuarios—.

Dos bases de código Django compartiendo `django_migrations` y las tablas
`auth_*` es una fuente conocida de corrupción: cada despliegue ejecuta su
propio `migrate` y uno puede alterar lo que el otro creó.

`config/settings.py` conserva la solución: la variable `DJANGO_DB_SCHEMA` fija
el `search_path` a un esquema dedicado, de modo que **todas** las tablas
—incluidas `django_migrations` y `auth_user`— se crean aisladas.

**La regla que no debe romperse:** ese `search_path` contiene *exclusivamente*
el esquema propio. Si se añadiera `public`, el `migrate` leería el historial
ajeno, concluiría que `auth` ya está aplicado y dejaría el esquema incompleto,
con un error que no apunta a su causa.

### 3. La contraseña en la URI debe ir codificada

`DATABASE_URL` es una URI. Una contraseña con `@`, `:`, `/`, `#`, `?`, `%` o
`&` rompe su interpretación y produce fallos de autenticación que no se parecen
a un problema de contraseña. Deben escribirse codificados (`%40`, `%3A`, `%2F`,
`%23`, `%3F`, `%25`, `%26`).

Tras varios intentos fallidos, el agrupador de Supabase activa un cortacircuitos
(`ECIRCUITBREAKER`) y bloquea temporalmente **todas** las conexiones nuevas: a
partir de ahí, incluso una contraseña correcta falla. Conviene esperar unos
minutos antes de reintentar, o se persigue un fantasma.

---

## Si se retoma

La recomendación sería **una base de datos dedicada, no compartida**: un
servicio PostgreSQL en el mismo proyecto de la plataforma. Railway inyecta su
`DATABASE_URL` como variable de referencia, de modo que ninguna contraseña se
copia a mano y los tres problemas de arriba desaparecen de golpe.

Variables necesarias: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`,
`DJANGO_ALLOWED_HOSTS`, `DJANGO_TIME_ZONE`, `DJANGO_LOG_LEVEL` y `DATABASE_URL`.
El dominio público se lee solo de `RAILWAY_PUBLIC_DOMAIN`.

Sondas: `/healthz` responde si el proceso vive; `/api/v1/salud/` verifica la
base de datos y devuelve `503` cuando el equipo no está en condiciones de
operar. Es la segunda la que debe vigilarse.

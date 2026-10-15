#!/bin/sh
#
# Secuencia de arranque del servicio en producción.
#
# Es la ÚNICA fuente de verdad del arranque: la invocan tanto el `Dockerfile`
# como el `Procfile`. Tenerla en un solo lugar no es una preferencia de estilo;
# es la corrección de un fallo real. Antes, el `Dockerfile` arrancaba
# `manage.py runserver` y el `Procfile` arrancaba Gunicorn, y Railway —que
# prefiere el Dockerfile cuando existe— acabó desplegando el servidor de
# desarrollo de Django en producción, en un puerto fijo y sin aplicar
# migraciones. Dos comandos de arranque distintos son dos verdades, y la
# plataforma elige la que usted no previó.
#
set -eu

# `migrate` en cada arranque es correcto para este servicio: el despliegue
# reemplaza el contenedor, y el esquema debe quedar al día sin que nadie
# ejecute comandos a mano. Es idempotente.
#
# Se ejecuta aquí y no en la construcción de la imagen porque necesita la base
# de datos real, que solo existe en tiempo de ejecución. Si falla, `set -e`
# aborta el arranque: es lo que debe ocurrir. Un contenedor que sirva peticiones
# con un esquema desactualizado produciría errores incomprensibles en la caja,
# y es preferible que el despliegue no pase la sonda de salud.
echo "==> Aplicando migraciones"
python manage.py migrate --noinput

# Los archivos estáticos ya se recolectaron durante la construcción de la
# imagen, de modo que aquí no se repite el trabajo en cada arranque.

# Gunicorn, no `runserver`. El servidor de desarrollo de Django es monohilo,
# recarga código en caliente y su propia documentación advierte que no ha sido
# auditado para seguridad: no debe atender tráfico real.
#
# El puerto se toma de $PORT, que es lo que la plataforma inyecta y donde
# dirige el tráfico. Fijarlo en el código haría que la sonda de salud fallara
# sin un mensaje que explicara por qué.
#
# 2 procesos x 4 hilos: suficiente para el volumen de un estacionamiento y
# conservador con el agrupador de conexiones de Supabase, que tiene un límite
# de conexiones simultáneas por proyecto.
echo "==> Iniciando Gunicorn en el puerto ${PORT:-8000}"
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers 2 \
    --threads 4 \
    --timeout 60 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -

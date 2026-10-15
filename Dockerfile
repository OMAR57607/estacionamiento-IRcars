# Imagen de producción del servicio web.
#
# Importante: Railway construye con este archivo cuando existe, e **ignora el
# `Procfile`**. Por eso el comando de arranque de esta imagen debe ser el de
# producción, no el de desarrollo. Docker Compose sobrescribe `command:` para
# el trabajo local, de modo que un solo Dockerfile sirve a ambos escenarios sin
# que el de desarrollo se filtre a producción.

# Imagen slim: menor superficie de ataque y arranque más rápido que la completa.
FROM python:3.12-slim

# Sin .pyc en disco y salida sin búfer: los logs aparecen en tiempo real en
# `docker compose logs` y en el panel de Railway, requisito básico de
# observabilidad.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Las dependencias se copian primero para aprovechar la caché de capas:
# cambiar el código fuente no reinstala paquetes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Recolección de estáticos en la construcción, no en el arranque: los archivos
# quedan dentro de la imagen, inmutables y sin repetir el trabajo cada vez que
# el contenedor reinicia.
#
# El `DATABASE_URL` de esta línea es un valor desechable y deliberado. La
# configuración exige esa variable al importarse, pero `collectstatic` no toca
# la base de datos. El valor vive solo en esta capa de construcción y NO queda
# en el entorno de la imagen: en ejecución, `DATABASE_URL` sigue siendo
# obligatoria. Esa obligatoriedad se conserva a propósito —un valor por omisión
# haría que un despliegue sin configurar "funcionara" escribiendo en una base
# efímera, y perder cobros en silencio es mucho peor que un arranque fallido—.
RUN DATABASE_URL="sqlite:////tmp/solo-construccion.sqlite3" \
    DJANGO_SECRET_KEY="solo-para-construir-estaticos-no-es-un-secreto" \
    python manage.py collectstatic --noinput

# La aplicación no corre como root (principio de mínimo privilegio). El cambio
# de propietario va después de `collectstatic` para que los estáticos ya
# generados queden accesibles al usuario sin privilegios.
RUN useradd --create-home --shell /bin/bash ircars \
    && chmod +x scripts/arrancar.sh \
    && chown -R ircars:ircars /app
USER ircars

EXPOSE 8000

CMD ["./scripts/arrancar.sh"]

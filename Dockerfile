# Imagen slim: menor superficie de ataque y arranque más rápido que la completa.
FROM python:3.12-slim

# Sin .pyc en disco y salida sin búfer: los logs aparecen en tiempo real
# en `docker compose logs`, requisito básico de observabilidad.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Las dependencias se copian primero para aprovechar la caché de capas:
# cambiar el código fuente no reinstala paquetes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# La aplicación no corre como root (principio de mínimo privilegio).
RUN useradd --create-home --shell /bin/bash ircars && chown -R ircars:ircars /app
USER ircars

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

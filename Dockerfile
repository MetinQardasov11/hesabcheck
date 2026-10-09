FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY --chown=app:app . .
RUN DJANGO_DEBUG=1 python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown app:app /app/media
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "660", "--graceful-timeout", "660", "--access-logfile", "-", "--error-logfile", "-", "--forwarded-allow-ips", "*"]

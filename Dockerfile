FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home appuser && mkdir -p /app/instance \
    && chown -R appuser /app/instance
USER appuser

ENV PORT=8000
EXPOSE 8000
CMD gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 "app:create_app()"

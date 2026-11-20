FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements-koyeb.txt .
RUN pip install --no-cache-dir -r requirements-koyeb.txt

COPY pyfiles ./pyfiles
COPY web ./web
COPY servers.json ./servers.json

WORKDIR /app/pyfiles

EXPOSE 8000

# Render health check: GET /api/health. Render sets PORT.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]

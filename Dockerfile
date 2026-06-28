FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     MEMORYV4_DB_PATH=/data/memoryv4.sqlite3

WORKDIR /app

COPY requirements.txt requirements-dev.txt pyproject.toml Makefile ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3   CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2); raise SystemExit(0 if json.load(r).get('status') == 'ok' else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

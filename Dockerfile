FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="MemoryV4 Core" \
      org.opencontainers.image.source="https://github.com/BartSchuster22/memoryV4" \
      org.opencontainers.image.revision="$VCS_REF"

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     MEMORYV4_DB_PATH=/data/memoryv4.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN chmod -R a=rX /app && mkdir -p /data /backups && chown -R 65532:65532 /data /backups
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3   CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2); raise SystemExit(0 if json.load(r).get('status') == 'ok' else 1)"
USER 65532:65532
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

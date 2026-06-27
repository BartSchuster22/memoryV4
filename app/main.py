"""FastAPI entrypoint for the MemoryV4 core service."""
from __future__ import annotations

from fastapi import FastAPI

from app.settings import load_settings
from app.storage import probe_sqlite

APP_NAME = "memoryv4-core"
APP_VERSION = "0.1.0-d0"


def create_app() -> FastAPI:
    """Create and configure the MemoryV4 core FastAPI application."""
    settings = load_settings()
    app = FastAPI(
        title="MemoryV4 Core",
        version=settings.version,
        description="Slim governed memory core baseline. UI and orchestration are out of scope.",
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        storage = probe_sqlite(settings.database_path)
        return {
            "status": "ok" if storage.status == "ok" else "degraded",
            "service": settings.service_name,
            "version": settings.version,
            "storage_backend": storage.backend,
        }

    return app


app = create_app()

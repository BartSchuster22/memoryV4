"""FastAPI entrypoint for the MemoryV4 core service."""

from fastapi import FastAPI

APP_NAME = "memoryv4-core"
APP_VERSION = "0.1.0-d0"


def create_app() -> FastAPI:
    """Create and configure the MemoryV4 core FastAPI application."""
    app = FastAPI(
        title="MemoryV4 Core",
        version=APP_VERSION,
        description="Slim governed memory core baseline. UI and orchestration are out of scope.",
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": APP_NAME, "version": APP_VERSION}

    return app


app = create_app()

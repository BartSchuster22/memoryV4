"""FastAPI entrypoint for the MemoryV4 core service."""

from __future__ import annotations

from hmac import compare_digest

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status

from app.schemas import Record, RecordCreate, ScopePath, SearchResult
from app.settings import load_settings
from app.storage import SqliteStore, probe_sqlite

APP_NAME = "memoryv4-core"
APP_VERSION = "0.1.0-t2"


class AuthContext:
    def __init__(self, actor: str, scope_path: str):
        self.actor = actor
        self.scope_path = scope_path


def _authorize(
    requested_scope: str,
    auth: AuthContext,
) -> None:
    if not ScopePath.is_descendant_or_equal(requested_scope, auth.scope_path):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="scope outside key grant")


def create_app() -> FastAPI:
    """Create and configure the MemoryV4 core FastAPI application."""
    settings = load_settings()
    app = FastAPI(
        title="MemoryV4 Core",
        version=settings.version,
        description="Slim governed memory core. UI and orchestration are out of scope.",
    )

    def require_auth(authorization: str | None = Header(default=None)) -> AuthContext:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token required"
            )
        token = authorization.removeprefix("Bearer ")
        for candidate, scope_path in settings.api_keys.items():
            if compare_digest(token, candidate):
                return AuthContext(
                    actor=f"api-key:{candidate[:8]}", scope_path=ScopePath.validate(scope_path)
                )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")

    def get_store() -> SqliteStore:
        return SqliteStore(settings.database_path)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        storage = probe_sqlite(settings.database_path)
        return {
            "status": "ok" if storage["status"] == "ok" else "degraded",
            "service": settings.service_name,
            "version": settings.version,
            "storage_backend": storage["backend"],
        }

    @app.post("/records", status_code=status.HTTP_201_CREATED, tags=["records"])
    def create_record(
        record: RecordCreate,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
    ) -> Record:
        _authorize(record.scope_path, auth)
        return store.create_record(record, actor=auth.actor)

    @app.get("/records", tags=["records"])
    def list_records(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
    ) -> dict[str, list[Record]]:
        scope_path = ScopePath.validate(scope_path or auth.scope_path)
        _authorize(scope_path, auth)
        return {"records": store.list_records(scope_path=scope_path, include_public=include_public)}

    @app.get("/records/{record_id}", tags=["records"])
    def get_record(
        record_id: str,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
    ) -> Record:
        record = store.get_record(record_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
        _authorize(record.scope_path, auth)
        return record

    @app.get("/search", tags=["retrieval"])
    def search(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        q: str = Query(min_length=1),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        limit: int = Query(25, ge=1, le=100),
    ) -> dict[str, list[SearchResult]]:
        scope_path = ScopePath.validate(scope_path or auth.scope_path)
        _authorize(scope_path, auth)
        results = store.search_records(
            q,
            scope_path=scope_path,
            actor=auth.actor,
            include_public=include_public,
            limit=limit,
        )
        return {"results": results}

    return app


app = create_app()

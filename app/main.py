"""FastAPI entrypoint for the MemoryV4 core service."""

from __future__ import annotations

from hmac import compare_digest
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.contracts import (
    CapabilitiesResponse,
    ErrorBody,
    ErrorResponse,
    SchemaContractResponse,
    capabilities,
    schema_contract,
)
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

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-MemoryV4-Contract-Version"] = "1.0.0"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code_by_status = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            412: "precondition_failed",
            428: "precondition_required",
        }
        body = ErrorResponse(
            error=ErrorBody(
                code=code_by_status.get(exc.status_code, "request_failed"),
                message=str(exc.detail),
                status=exc.status_code,
                request_id=request.state.request_id,
                details={},
            )
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorBody(
                code="invalid_request",
                message="request validation failed",
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                request_id=request.state.request_id,
                details={"violations": exc.errors()},
            )
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=body.model_dump(mode="json"),
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

    @app.get(
        "/capabilities",
        response_model=CapabilitiesResponse,
        tags=["contract"],
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    )
    def get_capabilities(auth: AuthContext = Depends(require_auth)) -> CapabilitiesResponse:
        del auth
        return capabilities(settings.service_name)

    @app.get(
        "/schema",
        response_model=SchemaContractResponse,
        tags=["contract"],
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    )
    def get_schema(auth: AuthContext = Depends(require_auth)) -> SchemaContractResponse:
        del auth
        return schema_contract()

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

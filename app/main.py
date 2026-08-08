"""FastAPI entrypoint for the MemoryV4 governed core service."""

from __future__ import annotations

import hashlib
import json
from hmac import compare_digest
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.contracts import (
    CONTRACT_VERSION,
    CapabilitiesResponse,
    ErrorBody,
    ErrorResponse,
    Permission,
    SchemaContractResponse,
    WritePolicy,
    capabilities,
    schema_contract,
)
from app.pagination import CursorError
from app.schemas import (
    Artifact,
    ArtifactCreate,
    ArtifactPage,
    Entity,
    EntityContext,
    EntityCreate,
    EntityPage,
    EntityPatch,
    Lifecycle,
    ObjectKind,
    Record,
    RecordCreate,
    RecordPage,
    RecordPatch,
    Relation,
    RelationCreate,
    RelationPage,
    Role,
    ScopePath,
    SearchPage,
    SortOrder,
)
from app.settings import ApiKeyGrant, load_settings
from app.storage import (
    IdempotencyConflictError,
    ObjectConflictError,
    RecordStateConflictError,
    SqliteStore,
    VersionConflictError,
    probe_sqlite,
)

APP_NAME = "memoryv4-core"
APP_VERSION = "0.3.0-core-objects"


class AuthContext:
    def __init__(self, grant: ApiKeyGrant, *, delegated_actor: str | None = None):
        self.actor = delegated_actor or grant.actor
        self.scope_path = ScopePath.validate(grant.scope_path)
        self.permissions = grant.permissions

    def has(self, permission: Permission) -> bool:
        return Permission.admin in self.permissions or permission in self.permissions


def _api_error(status_code: int, code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "details": details},
    )


def _authorize_effective_scope(requested_scope: str, auth: AuthContext) -> None:
    if not ScopePath.is_descendant_or_equal(requested_scope, auth.scope_path):
        raise _api_error(403, "forbidden", "scope outside actor grant")


def _require_permission(auth: AuthContext, permission: Permission) -> None:
    if not auth.has(permission):
        raise _api_error(
            403,
            "forbidden",
            "required permission missing",
            required_permission=permission.value,
        )


def _validate_idempotency_key(value: str | None) -> str:
    if value is None:
        raise _api_error(428, "precondition_required", "Idempotency-Key header required")
    if value != value.strip() or not 8 <= len(value) <= 200:
        raise _api_error(
            422,
            "invalid_request",
            "Idempotency-Key must be trimmed and contain 8 to 200 characters",
        )
    return value


def _parse_version(value: str | None) -> int:
    if value is None:
        raise _api_error(428, "precondition_required", "If-Match header required")
    normalized = value.strip()
    if normalized.startswith('W/"') or normalized.startswith("W/'"):
        raise _api_error(422, "invalid_request", "weak If-Match values are not supported")
    normalized = normalized.strip('"')
    try:
        version = int(normalized)
    except ValueError as exc:
        raise _api_error(
            422, "invalid_request", "If-Match must contain an integer version"
        ) from exc
    if version < 1:
        raise _api_error(422, "invalid_request", "If-Match version must be positive")
    return version


def _request_hash(operation: str, payload: object) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _authorize_create(record: RecordCreate, auth: AuthContext) -> None:
    _authorize_effective_scope(record.scope_path, auth)
    if record.lifecycle not in {Lifecycle.live, Lifecycle.working}:
        raise _api_error(
            422,
            "invalid_request",
            "new records must start live or working; governed transitions own terminal states",
        )
    if auth.has(Permission.create):
        if record.role == Role.canonical:
            _require_permission(auth, Permission.promote)
            if record.lifecycle != Lifecycle.live:
                raise _api_error(422, "invalid_request", "canonical records must be live")
        return
    if auth.has(Permission.create_working):
        if record.role != Role.active or record.lifecycle != Lifecycle.working:
            raise _api_error(
                403,
                "forbidden",
                "memory.create-working may create only active/working candidates",
            )
        return
    _require_permission(auth, Permission.create)


def _authorize_record_edit(record: Record, auth: AuthContext) -> None:
    _require_permission(auth, Permission.edit)
    if record.role == Role.canonical:
        raise _api_error(409, "conflict", "canonical records must be revised by supersession")
    if record.write_policy == WritePolicy.immutable:
        raise _api_error(409, "conflict", "immutable records cannot be edited")
    if Permission.admin in auth.permissions:
        return
    if record.write_policy == WritePolicy.admin_only:
        raise _api_error(403, "forbidden", "record write policy requires memory.admin")
    if record.write_policy == WritePolicy.author_only and record.author_actor != auth.actor:
        raise _api_error(403, "forbidden", "record write policy permits only its author")


def create_app() -> FastAPI:
    """Create and configure the MemoryV4 core FastAPI application."""
    settings = load_settings()
    app = FastAPI(
        title="MemoryV4 Core",
        version=settings.version,
        description="Slim governed memory core. UI and orchestration are out of scope.",
    )

    def get_store() -> SqliteStore:
        return SqliteStore(settings.database_path)

    @app.middleware("http")
    async def governance_envelope(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        request.state.actor = "anonymous"
        request.state.scope_path = "_unknown"
        response = await call_next(request)
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and response.status_code >= 400:
            reason = request.headers.get("X-MemoryV4-Reason")
            get_store().write_denial(
                operation=f"{request.method} {request.url.path}",
                actor=request.state.actor,
                scope_path=request.state.scope_path,
                request_id=request.state.request_id,
                status_code=response.status_code,
                reason=reason[:500] if reason else None,
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-MemoryV4-Contract-Version"] = CONTRACT_VERSION
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code_by_status = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            412: "version_conflict",
            428: "precondition_required",
        }
        if isinstance(exc.detail, dict):
            default_code = code_by_status.get(exc.status_code, "request_failed")
            code = str(exc.detail.get("code", default_code))
            message = str(exc.detail.get("message", "request failed"))
            details = exc.detail.get("details", {})
        else:
            code = code_by_status.get(exc.status_code, "request_failed")
            message = str(exc.detail)
            details = {}
        body = ErrorResponse(
            error=ErrorBody(
                code=code,
                message=message,
                status=exc.status_code,
                request_id=request.state.request_id,
                details=details,
            )
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        violations = [
            {
                "type": error["type"],
                "location": [str(part) for part in error["loc"]],
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        body = ErrorResponse(
            error=ErrorBody(
                code="invalid_request",
                message="request validation failed",
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                request_id=request.state.request_id,
                details={"violations": violations},
            )
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=body.model_dump(mode="json"),
        )

    def require_auth(
        request: Request,
        authorization: str | None = Header(default=None),
        delegated_actor: str | None = Header(default=None, alias="X-MemoryV4-Actor"),
    ) -> AuthContext:
        if not authorization or not authorization.startswith("Bearer "):
            raise _api_error(401, "unauthorized", "bearer token required")
        token = authorization.removeprefix("Bearer ")
        for candidate, grant in settings.api_keys.items():
            if compare_digest(token, candidate):
                if grant.allow_actor_delegation:
                    if (
                        delegated_actor is None
                        or delegated_actor != delegated_actor.strip()
                        or not 1 <= len(delegated_actor) <= 200
                        or any(ord(char) < 32 for char in delegated_actor)
                    ):
                        raise _api_error(
                            422,
                            "invalid_request",
                            "X-MemoryV4-Actor is required for this delegated grant",
                        )
                elif delegated_actor is not None:
                    raise _api_error(
                        403,
                        "forbidden",
                        "this grant cannot delegate actor identity",
                    )
                auth = AuthContext(grant, delegated_actor=delegated_actor)
                request.state.actor = auth.actor
                request.state.scope_path = auth.scope_path
                return auth
        raise _api_error(401, "unauthorized", "invalid bearer token")

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
        _require_permission(auth, Permission.read)
        return capabilities(settings.service_name)

    @app.get(
        "/schema",
        response_model=SchemaContractResponse,
        tags=["contract"],
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    )
    def get_schema(auth: AuthContext = Depends(require_auth)) -> SchemaContractResponse:
        _require_permission(auth, Permission.read)
        return schema_contract()

    @app.post("/entities", status_code=status.HTTP_201_CREATED, tags=["entities"])
    def create_entity(
        entity: EntityCreate,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Entity:
        _require_permission(auth, Permission.create)
        _authorize_effective_scope(entity.scope_path, auth)
        key = _validate_idempotency_key(idempotency_key)
        digest = _request_hash("entity.create", entity.model_dump(mode="json"))
        try:
            created, replayed = store.create_entity_idempotent(
                entity,
                actor=auth.actor,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(409, "idempotency_conflict", "Idempotency-Key conflict") from exc
        except ObjectConflictError as exc:
            raise _api_error(409, "conflict", "entity identity already exists") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return created

    @app.get("/entities", response_model=EntityPage, tags=["entities"])
    def list_entities(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        entity_type: str | None = Query(None),
        name_contains: str | None = Query(None, min_length=1, max_length=240),
        sort: str = Query("updated_at", pattern="^(name|created_at|updated_at|id)$"),
        order: SortOrder = Query(SortOrder.desc),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> EntityPage:
        _require_permission(auth, Permission.read)
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        try:
            entities, next_cursor = store.page_entities(
                scope_path=effective_scope,
                include_public=include_public,
                entity_type=entity_type,
                name_contains=name_contains,
                sort=sort,
                order=order,
                limit=limit,
                cursor=cursor,
            )
        except CursorError as exc:
            raise _api_error(422, "invalid_request", str(exc)) from exc
        return EntityPage(entities=entities, next_cursor=next_cursor)

    @app.get("/entities/{entity_type}/{entity_id}", tags=["entities"])
    def get_entity(
        entity_type: str,
        entity_id: str,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
    ) -> Entity:
        _require_permission(auth, Permission.read)
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        entity = store.get_visible_entity(
            entity_type,
            entity_id,
            scope_path=effective_scope,
            include_public=include_public,
        )
        if entity is None:
            raise _api_error(404, "not_found", "entity not found")
        return entity

    @app.patch("/entities/{entity_type}/{entity_id}", tags=["entities"])
    def patch_entity(
        entity_type: str,
        entity_id: str,
        patch: EntityPatch,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> Entity:
        _require_permission(auth, Permission.edit)
        key = _validate_idempotency_key(idempotency_key)
        expected_version = _parse_version(if_match)
        current = store.get_entity(entity_type, entity_id)
        if current is None or not ScopePath.is_descendant_or_equal(
            current.scope_path, auth.scope_path
        ):
            raise _api_error(404, "not_found", "entity not found")
        digest = _request_hash(
            f"entity.patch:{entity_type}:{entity_id}",
            {
                "version": expected_version,
                "patch": patch.model_dump(mode="json", exclude_unset=True),
            },
        )
        try:
            updated, replayed = store.update_entity_idempotent(
                entity_type,
                entity_id,
                patch,
                actor=auth.actor,
                expected_version=expected_version,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(409, "idempotency_conflict", "Idempotency-Key conflict") from exc
        except VersionConflictError as exc:
            raise _api_error(412, "version_conflict", "entity version does not match") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return updated

    @app.post("/records", status_code=status.HTTP_201_CREATED, tags=["records"])
    def create_record(
        record: RecordCreate,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Record:
        key = _validate_idempotency_key(idempotency_key)
        _authorize_create(record, auth)
        digest = _request_hash("record.create", record.model_dump(mode="json"))
        try:
            created, replayed = store.create_record_idempotent(
                record,
                actor=auth.actor,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(
                409,
                "idempotency_conflict",
                "Idempotency-Key was already used for a different request",
            ) from exc
        except KeyError as exc:
            raise _api_error(404, "not_found", "linked entity not found in record scope") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return created

    @app.get("/records", response_model=RecordPage, tags=["records"])
    def list_records(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        role: Role | None = Query(None),
        lifecycle: Lifecycle | None = Query(None),
        entity_type: str | None = Query(None),
        entity_id: str | None = Query(None),
        topic: str | None = Query(None),
        tag: str | None = Query(None),
        min_confidence: float | None = Query(None, ge=0, le=1),
        include_deleted: bool = Query(False),
        sort: str = Query(
            "updated_at", pattern="^(title|created_at|updated_at|confidence|id)$"
        ),
        order: SortOrder = Query(SortOrder.desc),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> RecordPage:
        _require_permission(auth, Permission.read)
        if include_deleted:
            _require_permission(auth, Permission.archive)
        if (entity_type is None) != (entity_id is None):
            raise _api_error(
                422, "invalid_request", "entity_type and entity_id must be supplied together"
            )
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        try:
            records, next_cursor = store.page_records(
                scope_path=effective_scope,
                include_public=include_public,
                role=role,
                lifecycle=lifecycle,
                entity_type=entity_type,
                entity_id=entity_id,
                topic=topic,
                tag=tag,
                min_confidence=min_confidence,
                include_deleted=include_deleted,
                sort=sort,
                order=order,
                limit=limit,
                cursor=cursor,
            )
        except CursorError as exc:
            raise _api_error(422, "invalid_request", str(exc)) from exc
        return RecordPage(records=records, next_cursor=next_cursor)

    @app.get("/records/{record_id}", tags=["records"])
    def get_record(
        record_id: str,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
    ) -> Record:
        _require_permission(auth, Permission.read)
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        record = store.get_visible_record(
            record_id,
            scope_path=effective_scope,
            include_public=include_public,
        )
        if record is None:
            raise _api_error(404, "not_found", "record not found")
        return record

    @app.patch("/records/{record_id}", tags=["records"])
    def patch_record(
        record_id: str,
        patch: RecordPatch,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> Record:
        key = _validate_idempotency_key(idempotency_key)
        expected_version = _parse_version(if_match)
        record = store.get_record(record_id)
        if record is None or not ScopePath.is_descendant_or_equal(
            record.scope_path, auth.scope_path
        ):
            raise _api_error(404, "not_found", "record not found")
        _authorize_record_edit(record, auth)
        if (
            patch.write_policy is not None
            and record.author_actor != auth.actor
            and Permission.admin not in auth.permissions
        ):
            raise _api_error(
                403,
                "forbidden",
                "only the author or memory.admin may change write policy",
            )
        digest = _request_hash(
            f"record.patch:{record_id}",
            {
                "version": expected_version,
                "patch": patch.model_dump(mode="json", exclude_unset=True),
            },
        )
        try:
            updated, replayed = store.update_record_idempotent(
                record_id,
                patch,
                actor=auth.actor,
                expected_version=expected_version,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(409, "idempotency_conflict", "Idempotency-Key conflict") from exc
        except KeyError as exc:
            raise _api_error(404, "not_found", "linked entity not found in record scope") from exc
        except VersionConflictError as exc:
            raise _api_error(412, "version_conflict", "record version does not match") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return updated

    @app.post("/records/{record_id}/promote", tags=["records"])
    def promote_record(
        record_id: str,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        if_match: str | None = Header(default=None, alias="If-Match"),
        reason: str | None = Header(default=None, alias="X-MemoryV4-Reason"),
    ) -> Record:
        _require_permission(auth, Permission.promote)
        key = _validate_idempotency_key(idempotency_key)
        expected_version = _parse_version(if_match)
        if reason is None or not reason.strip() or reason != reason.strip() or len(reason) > 500:
            raise _api_error(422, "invalid_request", "a trimmed promotion reason is required")
        record = store.get_record(record_id)
        if record is None or not ScopePath.is_descendant_or_equal(
            record.scope_path, auth.scope_path
        ):
            raise _api_error(404, "not_found", "record not found")
        digest = _request_hash(
            f"record.promote:{record_id}",
            {"version": expected_version, "reason": reason},
        )
        try:
            promoted, replayed = store.promote_record_idempotent(
                record_id,
                actor=auth.actor,
                expected_version=expected_version,
                reason=reason,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(409, "idempotency_conflict", "Idempotency-Key conflict") from exc
        except RecordStateConflictError as exc:
            raise _api_error(
                409,
                "conflict",
                "only active/working candidates can be promoted",
            ) from exc
        except VersionConflictError as exc:
            raise _api_error(412, "version_conflict", "record version does not match") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return promoted

    @app.post("/relations", status_code=status.HTTP_201_CREATED, tags=["relations"])
    def create_relation(
        relation: RelationCreate,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Relation:
        _require_permission(auth, Permission.create)
        _authorize_effective_scope(relation.scope_path, auth)
        key = _validate_idempotency_key(idempotency_key)
        digest = _request_hash(
            "relation.create", relation.model_dump(mode="json", by_alias=True)
        )
        try:
            created, replayed = store.create_relation_idempotent(
                relation,
                actor=auth.actor,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(409, "idempotency_conflict", "Idempotency-Key conflict") from exc
        except KeyError as exc:
            raise _api_error(404, "not_found", "relation endpoint not found in scope") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return created

    @app.get("/relations", response_model=RelationPage, tags=["relations"])
    def list_relations(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        relation_type: str | None = Query(None),
        from_kind: ObjectKind | None = Query(None),
        from_id: str | None = Query(None),
        to_kind: ObjectKind | None = Query(None),
        to_id: str | None = Query(None),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> RelationPage:
        _require_permission(auth, Permission.read)
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        try:
            relations, next_cursor = store.page_relations(
                scope_path=effective_scope,
                include_public=include_public,
                relation_type=relation_type,
                from_kind=from_kind,
                from_id=from_id,
                to_kind=to_kind,
                to_id=to_id,
                limit=limit,
                cursor=cursor,
            )
        except CursorError as exc:
            raise _api_error(422, "invalid_request", str(exc)) from exc
        return RelationPage(relations=relations, next_cursor=next_cursor)

    @app.post("/artifacts", status_code=status.HTTP_201_CREATED, tags=["artifacts"])
    def create_artifact(
        artifact: ArtifactCreate,
        response: Response,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Artifact:
        _require_permission(auth, Permission.create)
        _authorize_effective_scope(artifact.scope_path, auth)
        key = _validate_idempotency_key(idempotency_key)
        digest = _request_hash("artifact.create", artifact.model_dump(mode="json"))
        try:
            created, replayed = store.create_artifact_idempotent(
                artifact,
                actor=auth.actor,
                idempotency_key=key,
                request_hash=digest,
            )
        except IdempotencyConflictError as exc:
            raise _api_error(409, "idempotency_conflict", "Idempotency-Key conflict") from exc
        except KeyError as exc:
            raise _api_error(404, "not_found", "artifact target not found in scope") from exc
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        return created

    @app.get("/artifacts", response_model=ArtifactPage, tags=["artifacts"])
    def list_artifacts(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        artifact_type: str | None = Query(None),
        record_id: str | None = Query(None),
        entity_type: str | None = Query(None),
        entity_id: str | None = Query(None),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> ArtifactPage:
        _require_permission(auth, Permission.read)
        if (entity_type is None) != (entity_id is None):
            raise _api_error(
                422, "invalid_request", "entity_type and entity_id must be supplied together"
            )
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        try:
            artifacts, next_cursor = store.page_artifacts(
                scope_path=effective_scope,
                include_public=include_public,
                artifact_type=artifact_type,
                record_id=record_id,
                entity_type=entity_type,
                entity_id=entity_id,
                limit=limit,
                cursor=cursor,
            )
        except CursorError as exc:
            raise _api_error(422, "invalid_request", str(exc)) from exc
        return ArtifactPage(artifacts=artifacts, next_cursor=next_cursor)

    @app.get(
        "/context/{entity_type}/{entity_id}",
        response_model=EntityContext,
        tags=["context"],
    )
    def get_context(
        entity_type: str,
        entity_id: str,
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        limit: int = Query(50, ge=1, le=100),
    ) -> EntityContext:
        _require_permission(auth, Permission.read)
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        context = store.get_entity_context(
            entity_type,
            entity_id,
            scope_path=effective_scope,
            include_public=include_public,
            limit=limit,
        )
        if context is None:
            raise _api_error(404, "not_found", "entity not found")
        return context

    @app.get("/search", response_model=SearchPage, tags=["retrieval"])
    def search(
        auth: AuthContext = Depends(require_auth),
        store: SqliteStore = Depends(get_store),
        q: str = Query(min_length=1, max_length=500),
        scope_path: str | None = Query(None),
        include_public: bool = Query(False),
        role: Role | None = Query(None),
        lifecycle: Lifecycle | None = Query(None),
        entity_type: str | None = Query(None),
        entity_id: str | None = Query(None),
        tag: str | None = Query(None),
        limit: int = Query(25, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> SearchPage:
        _require_permission(auth, Permission.search)
        if (entity_type is None) != (entity_id is None):
            raise _api_error(
                422, "invalid_request", "entity_type and entity_id must be supplied together"
            )
        effective_scope = ScopePath.validate(scope_path or auth.scope_path)
        _authorize_effective_scope(effective_scope, auth)
        try:
            results, next_cursor = store.search_records_page(
                q,
                scope_path=effective_scope,
                actor=auth.actor,
                include_public=include_public,
                role=role,
                lifecycle=lifecycle,
                entity_type=entity_type,
                entity_id=entity_id,
                tag=tag,
                limit=limit,
                cursor=cursor,
            )
        except CursorError as exc:
            raise _api_error(422, "invalid_request", str(exc)) from exc
        return SearchPage(results=results, next_cursor=next_cursor)

    return app


app = create_app()

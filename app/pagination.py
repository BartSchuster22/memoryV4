"""Opaque, context-bound cursor helpers for MemoryV4 list APIs."""

from __future__ import annotations

import base64
import hashlib
import json


class CursorError(ValueError):
    pass


def _fingerprint(context: dict[str, object]) -> str:
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def encode_cursor(*, offset: int, context: dict[str, object]) -> str:
    payload = {"v": 1, "offset": offset, "context": _fingerprint(context)}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None, *, context: dict[str, object]) -> int:
    if value is None:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise CursorError("cursor is malformed") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or payload.get("context") != _fingerprint(context)
        or not isinstance(payload.get("offset"), int)
        or payload["offset"] < 0
    ):
        raise CursorError("cursor does not match this query")
    return payload["offset"]

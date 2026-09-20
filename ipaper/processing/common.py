from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime, timezone


class ProcessingError(ValueError):
    def __init__(self, code: str, status: int = 400, details: dict | None = None):
        super().__init__(code)
        self.code, self.status, self.details = code, status, dict(details or {})


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def identifier(value=None) -> str:
    if value is None:
        return str(uuid.uuid4())
    try:
        result = str(uuid.UUID(value))
        if result != value:
            raise ValueError
        return result
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProcessingError("invalid_identifier") from exc


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def fingerprint(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()

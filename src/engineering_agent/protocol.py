from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    PATH_OUTSIDE_ROOT = "PATH_OUTSIDE_ROOT"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    CONFLICT = "CONFLICT"
    LOCKED = "LOCKED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class ErrorEnvelope:
    code: str
    message: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"error": asdict(self)}


class ProtocolError(RuntimeError):
    def __init__(self, code: ErrorCode | str, message: str, *, details: dict[str, Any] | None = None, retryable: bool = False):
        normalized = code.value if isinstance(code, ErrorCode) else str(code)
        self.envelope = ErrorEnvelope(normalized, message, details=details or {}, retryable=retryable)
        super().__init__(message)


def success(result: Any, *, request_id: str | None = None) -> dict[str, Any]:
    return {"ok": True, "request_id": request_id or str(uuid.uuid4()), "result": result}


def failure(error: ErrorEnvelope | ProtocolError, *, request_id: str | None = None) -> dict[str, Any]:
    envelope = error.envelope if isinstance(error, ProtocolError) else error
    payload = envelope.to_dict()
    payload["ok"] = False
    payload["request_id"] = request_id or envelope.request_id
    return payload


def require(condition: bool, code: ErrorCode | str, message: str, *, details: dict[str, Any] | None = None):
    if not condition:
        raise ProtocolError(code, message, details=details)


def parse_json_line(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ErrorCode.INVALID_REQUEST, "request must be valid JSON", details={"position": exc.pos}) from exc
    require(isinstance(value, dict), ErrorCode.INVALID_REQUEST, "request must be a JSON object")
    return value

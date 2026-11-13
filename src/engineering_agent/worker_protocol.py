"""Versioned, portable job files shared by controllers and software workers."""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "simloom-ai/worker/v1"
MAX_FILES = 128
MAX_BYTES = 32 * 1024 * 1024
MAX_HTTP_BYTES = 48 * 1024 * 1024
MAX_CHUNK_BYTES = 4 * 1024 * 1024
MAX_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024


class WorkerError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def failure(exc: Exception, request_id: str = "") -> dict[str, Any]:
    return {"schema_version": SCHEMA, "request_id": request_id, "status": "failed",
            "outputs": [], "error": {"code": getattr(exc, "code", "WORKER_FAILED"), "message": str(exc)}}


def relative_path(value: Any, *, output: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value or re.search(r'[<>:"|?*\x00-\x1f]', value):
        raise WorkerError("INVALID_ARTIFACT_PATH")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(p in {".", ".."} for p in path.parts):
        raise WorkerError("INVALID_ARTIFACT_PATH", value)
    for part in path.parts:
        if part.endswith((".", " ")) or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part):
            raise WorkerError("INVALID_ARTIFACT_PATH", value)
    if path.parts[0].startswith((".", "_")) or value.lower().startswith("artifacts/runs/"):
        raise WorkerError("RESERVED_ARTIFACT_PATH", value)
    if output and (value.lower().startswith("artifacts/generated/") or value.lower() in {"artifacts", "artifacts/runs", "artifacts/generated"}):
        raise WorkerError("RESERVED_ARTIFACT_PATH", value)
    return value


def names(values: Any, *, output: bool = False) -> list[str]:
    if not isinstance(values, list) or len(values) > MAX_FILES:
        raise WorkerError("INVALID_FILE_LIST")
    result = [relative_path(value, output=output) for value in values]
    if len({value.casefold() for value in result}) != len(result):
        raise WorkerError("DUPLICATE_ARTIFACT_PATH")
    lowered = {value.casefold() for value in result}
    if any(str(parent).casefold() in lowered for value in result for parent in PurePosixPath(value).parents):
        raise WorkerError("CONFLICTING_ARTIFACT_PATHS")
    return result


def safe_file(root: Path, name: str) -> Path:
    name = relative_path(name)
    root = root.resolve()
    target = root / name
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise WorkerError("SYMLINK_ARTIFACT", name)
    if not target.resolve().is_relative_to(root):
        raise WorkerError("ARTIFACT_OUTSIDE_ROOT", name)
    return target


def pack_file(root: Path, name: str) -> dict[str, Any]:
    path = safe_file(root, name)
    if not path.is_file():
        raise WorkerError("ARTIFACT_MISSING", name)
    if path.stat().st_size > MAX_BYTES:
        raise WorkerError("ARTIFACT_TOO_LARGE", name)
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise WorkerError("ARTIFACT_TOO_LARGE", name)
    return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "content_base64": base64.b64encode(data).decode("ascii")}


def file_manifest(path: Path, name: str, *, max_bytes: int = MAX_TRANSFER_BYTES) -> dict[str, Any]:
    """Return a streaming manifest without loading the file into memory."""
    path = Path(path)
    relative_path(name)
    if not path.is_file():
        raise WorkerError("ARTIFACT_MISSING", name)
    size = path.stat().st_size
    if size > max_bytes:
        raise WorkerError("ARTIFACT_TOO_LARGE", name)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(MAX_CHUNK_BYTES), b""):
            digest.update(chunk)
    return {"path": name, "bytes": size, "sha256": digest.hexdigest()}


def decode_files(records: Any) -> dict[str, bytes]:
    if not isinstance(records, list) or len(records) > MAX_FILES or not all(isinstance(r, dict) for r in records):
        raise WorkerError("INVALID_ARTIFACT_MANIFEST")
    names([r.get("path") for r in records])
    decoded, total = {}, 0
    for record in records:
        content = record.get("content_base64")
        if not isinstance(content, str) or len(content) > (MAX_BYTES + 2) // 3 * 4:
            raise WorkerError("ARTIFACT_TOO_LARGE")
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, UnicodeError) as exc:
            raise WorkerError("INVALID_ARTIFACT_ENCODING") from exc
        total += len(data)
        if total > MAX_BYTES:
            raise WorkerError("TRANSFER_TOO_LARGE")
        if type(record.get("bytes")) is not int or record["bytes"] != len(data) or record.get("sha256") != hashlib.sha256(data).hexdigest():
            raise WorkerError("ARTIFACT_HASH_MISMATCH", record["path"])
        decoded[record["path"]] = data
    return decoded


def manifest(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: record[key] for key in ("path", "bytes", "sha256")} for record in records]

"""Persistent, hash-checked chunk transfer storage for the Windows worker."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .transactions import _json_write
from .worker_protocol import (MAX_CHUNK_BYTES, MAX_FILES, MAX_TRANSFER_BYTES,
                               WorkerError, file_manifest, relative_path, safe_file)


_TRANSFER_ID = re.compile(r"[a-f0-9]{32}")


def transfer_id(value: Any) -> str:
    if not isinstance(value, str) or not _TRANSFER_ID.fullmatch(value):
        raise WorkerError("INVALID_TRANSFER_ID")
    return value


class ArtifactTransferStore:
    """Store uploads below the worker control directory and survive API restarts."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.directory = self.root / "control" / "transfers"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _folder(self, identifier: str) -> Path:
        return self.directory / transfer_id(identifier)

    def _metadata_path(self, identifier: str) -> Path:
        return self._folder(identifier) / "manifest.json"

    def _read(self, identifier: str) -> dict[str, Any]:
        path = self._metadata_path(identifier)
        if not path.is_file():
            raise WorkerError("TRANSFER_NOT_FOUND", identifier)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkerError("TRANSFER_METADATA_INVALID", identifier) from exc
        if value.get("transfer_id") != identifier:
            raise WorkerError("TRANSFER_METADATA_INVALID", identifier)
        return value

    @staticmethod
    def _normalize_manifest(files: Any) -> list[dict[str, Any]]:
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise WorkerError("INVALID_TRANSFER_MANIFEST")
        output = []
        total = 0
        seen = set()
        for record in files:
            if not isinstance(record, dict):
                raise WorkerError("INVALID_TRANSFER_MANIFEST")
            name = relative_path(record.get("path"))
            if name.casefold() in seen:
                raise WorkerError("DUPLICATE_ARTIFACT_PATH", name)
            seen.add(name.casefold())
            size = record.get("bytes")
            digest = record.get("sha256")
            if type(size) is not int or size < 0 or size > MAX_TRANSFER_BYTES or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise WorkerError("INVALID_TRANSFER_MANIFEST", name)
            total += size
            if total > MAX_TRANSFER_BYTES:
                raise WorkerError("TRANSFER_TOO_LARGE")
            output.append({"path": name, "bytes": size, "sha256": digest})
        if any(str(parent).casefold() in seen for item in output for parent in Path(item["path"]).parents if str(parent) != "."):
            raise WorkerError("CONFLICTING_ARTIFACT_PATHS")
        return output

    def begin(self, identifier: str, files: Any) -> dict[str, Any]:
        identifier = transfer_id(identifier)
        manifest = self._normalize_manifest(files)
        folder = self._folder(identifier)
        if folder.exists():
            previous = self._read(identifier)
            if previous.get("files") != manifest:
                raise WorkerError("TRANSFER_ID_CONFLICT")
            return self.status(identifier)
        folder.mkdir(parents=True)
        value = {"schema_version": "simloom-ai/worker-transfer/v1", "transfer_id": identifier,
                 "status": "open", "files": manifest}
        _json_write(self._metadata_path(identifier), value)
        return self.status(identifier)

    def status(self, identifier: str) -> dict[str, Any]:
        value = self._read(identifier)
        files = []
        for record in value["files"]:
            path = safe_file(self._folder(identifier), "files/" + record["path"])
            written = path.stat().st_size if path.is_file() else 0
            files.append({**record, "written": written,
                          "complete": written == record["bytes"] and value.get("status") == "complete"})
        return {"schema_version": value["schema_version"], "transfer_id": identifier,
                "status": value.get("status", "open"), "files": files}

    def put_chunk(self, identifier: str, path: str, offset: int, content_base64: str, chunk_sha256: str) -> dict[str, Any]:
        identifier = transfer_id(identifier)
        value = self._read(identifier)
        if value.get("status") != "open":
            raise WorkerError("TRANSFER_NOT_OPEN")
        record = next((item for item in value["files"] if item["path"] == path), None)
        if record is None:
            raise WorkerError("TRANSFER_FILE_NOT_DECLARED", path)
        if type(offset) is not int or offset < 0:
            raise WorkerError("INVALID_TRANSFER_OFFSET")
        if not isinstance(content_base64, str):
            raise WorkerError("INVALID_TRANSFER_CHUNK")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (ValueError, UnicodeError) as exc:
            raise WorkerError("INVALID_ARTIFACT_ENCODING") from exc
        if not data or len(data) > MAX_CHUNK_BYTES or not isinstance(chunk_sha256, str) or hashlib.sha256(data).hexdigest() != chunk_sha256:
            raise WorkerError("TRANSFER_CHUNK_HASH_MISMATCH", path)
        target = safe_file(self._folder(identifier), "files/" + path)
        target.parent.mkdir(parents=True, exist_ok=True)
        current = target.stat().st_size if target.is_file() else 0
        if offset != current:
            # A retried chunk is idempotent when the existing bytes match.
            if offset < current and offset + len(data) <= current:
                with target.open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(data)) == data:
                        return {"transfer_id": identifier, "path": path, "offset": offset,
                                "next_offset": current, "bytes": record["bytes"], "complete": current == record["bytes"]}
            raise WorkerError("TRANSFER_OFFSET_MISMATCH", path)
        if offset + len(data) > record["bytes"]:
            raise WorkerError("TRANSFER_CHUNK_EXCEEDS_FILE", path)
        with target.open("ab") as stream:
            stream.write(data)
        return {"transfer_id": identifier, "path": path, "offset": offset,
                "next_offset": offset + len(data), "bytes": record["bytes"],
                "complete": offset + len(data) == record["bytes"]}

    def complete(self, identifier: str) -> dict[str, Any]:
        identifier = transfer_id(identifier)
        value = self._read(identifier)
        if value.get("status") == "complete":
            return self.status(identifier)
        for record in value["files"]:
            path = safe_file(self._folder(identifier), "files/" + record["path"])
            actual = file_manifest(path, record["path"])
            if actual != record:
                raise WorkerError("TRANSFER_FILE_HASH_MISMATCH", record["path"])
        value["status"] = "complete"
        _json_write(self._metadata_path(identifier), value)
        return self.status(identifier)

    def records(self, identifier: str) -> list[dict[str, Any]]:
        value = self._read(identifier)
        if value.get("status") != "complete":
            raise WorkerError("TRANSFER_NOT_COMPLETE")
        return list(value["files"])

    def read_file(self, identifier: str, path: str) -> Path:
        identifier = transfer_id(identifier)
        if path not in {item["path"] for item in self.records(identifier)}:
            raise WorkerError("TRANSFER_FILE_NOT_DECLARED", path)
        target = safe_file(self._folder(identifier), "files/" + path)
        if not target.is_file():
            raise WorkerError("TRANSFER_FILE_MISSING", path)
        return target

    @staticmethod
    def read_chunk(path: Path, offset: int, limit: int) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_CHUNK_BYTES:
            raise WorkerError("INVALID_TRANSFER_CHUNK_RANGE")
        size = path.stat().st_size
        if offset > size:
            raise WorkerError("TRANSFER_OFFSET_OUT_OF_RANGE")
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        return {"offset": offset, "next_offset": offset + len(data), "bytes": size,
                "eof": offset + len(data) >= size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "chunk_sha256": hashlib.sha256(data).hexdigest(),
                "content_base64": base64.b64encode(data).decode("ascii")}


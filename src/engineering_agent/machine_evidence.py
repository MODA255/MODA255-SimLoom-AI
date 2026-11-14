"""Durable evidence that a vendor action was exercised on a real Windows worker.

The worker never infers this state from a configured path, COM dispatch, or a
fixture.  An operator records evidence only after a completed job has passed
the independent vendor evidence contract.  Records keep the exact job output
manifest, so a later file change invalidates the record instead of silently
leaving a stale ``real_machine_verified`` flag.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import tempfile
import time
from pathlib import Path
from typing import Any

from .worker_jobs import job_id
from .worker_protocol import SCHEMA as WORKER_SCHEMA, WorkerError, safe_file


SCHEMA = "simloom-ai/machine-evidence/v1"
_LICENSE_STATES = {"available", "borrowed"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _text(value: Any, code: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WorkerError(code)
    return value.strip()


class MachineEvidenceStore:
    """Store and revalidate operator-attested Windows smoke-test records."""

    def __init__(self, root: str | Path, jobs: Any):
        self.root = Path(root).resolve()
        self.jobs = jobs
        self.directory = safe_file(self.root, "control/evidence")
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, evidence_id: str) -> Path:
        if not isinstance(evidence_id, str) or len(evidence_id) != 64 or any(c not in "0123456789abcdef" for c in evidence_id):
            raise WorkerError("INVALID_EVIDENCE_ID")
        return safe_file(self.root, f"control/evidence/{evidence_id}.json")

    @staticmethod
    def _output_manifest(result: dict[str, Any]) -> list[dict[str, Any]]:
        outputs = result.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise WorkerError("MACHINE_EVIDENCE_OUTPUTS_MISSING")
        manifest = []
        for item in outputs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise WorkerError("MACHINE_EVIDENCE_OUTPUTS_INVALID")
            if type(item.get("bytes")) is not int or item["bytes"] <= 0 or not isinstance(item.get("sha256"), str):
                raise WorkerError("MACHINE_EVIDENCE_OUTPUTS_INVALID")
            manifest.append({key: item[key] for key in ("path", "bytes", "sha256")})
        return manifest

    def _job_snapshot(self, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        identifier = job_id(request_id)
        status = self.jobs.status(identifier)
        if status.get("status") != "completed":
            raise WorkerError("MACHINE_EVIDENCE_JOB_NOT_COMPLETED")
        result = self.jobs.result(identifier)
        if result.get("schema_version") != WORKER_SCHEMA or result.get("request_id") != identifier:
            raise WorkerError("MACHINE_EVIDENCE_JOB_MISMATCH")
        if result.get("verification") != "artifact_and_vendor_evidence":
            raise WorkerError("MACHINE_EVIDENCE_VENDOR_CONTRACT_REQUIRED")
        validation = result.get("vendor_validation")
        if not isinstance(validation, dict) or validation.get("passed") is not True:
            raise WorkerError("MACHINE_EVIDENCE_VENDOR_VALIDATION_REQUIRED")
        return status, result

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Bridge operations use the worker protocol envelope.  The nested
        # evidence schema is explicit so a controller cannot accidentally
        # submit an older evidence shape under a newer worker protocol.
        if not isinstance(payload, dict) or payload.get("evidence_schema", SCHEMA) != SCHEMA:
            raise WorkerError("MACHINE_EVIDENCE_SCHEMA_UNSUPPORTED")
        if platform.system().lower() != "windows":
            raise WorkerError("MACHINE_EVIDENCE_REQUIRES_WINDOWS")
        request_id = _text(payload.get("request_id"), "MACHINE_EVIDENCE_REQUEST_REQUIRED", maximum=64)
        status, result = self._job_snapshot(request_id)
        if payload.get("tool") != result.get("tool") or payload.get("action") != result.get("action"):
            raise WorkerError("MACHINE_EVIDENCE_ACTION_MISMATCH")
        license_status = _text(payload.get("license_status"), "MACHINE_EVIDENCE_LICENSE_REQUIRED", maximum=32).lower()
        if license_status not in _LICENSE_STATES:
            raise WorkerError("MACHINE_EVIDENCE_LICENSE_UNCONFIRMED")
        software_version = _text(payload.get("software_version"), "MACHINE_EVIDENCE_VERSION_REQUIRED", maximum=256)
        smoke_test_id = _text(payload.get("smoke_test_id"), "MACHINE_EVIDENCE_SMOKE_TEST_REQUIRED", maximum=256)
        operator = _text(payload.get("operator", getpass.getuser()), "MACHINE_EVIDENCE_OPERATOR_REQUIRED", maximum=256)
        notes = payload.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 4000:
            raise WorkerError("MACHINE_EVIDENCE_NOTES_INVALID")
        outputs = self._output_manifest(result)
        body = {
            "schema_version": SCHEMA,
            "request_id": request_id,
            "tool": result["tool"],
            "action": result["action"],
            "operator": operator,
            "software_version": software_version,
            "license_status": license_status,
            "smoke_test_id": smoke_test_id,
            "notes": notes,
            "recorded_at": time.time(),
            "worker": {"platform": platform.system(), "hostname": platform.node(), "user": getpass.getuser()},
            "job": {"status": status["status"], "request_digest": result.get("request_digest"),
                    "verification": result["verification"], "vendor_validation": result["vendor_validation"]},
            "outputs": outputs,
        }
        # The identity excludes the observation timestamp so retrying the
        # record operation for the same job is idempotent.
        identity = {key: value for key, value in body.items() if key != "recorded_at"}
        evidence_id = hashlib.sha256(_canonical(identity)).hexdigest()
        body["evidence_id"] = evidence_id
        path = self._path(evidence_id)
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            previous_identity = {key: value for key, value in previous.items()
                                 if key not in {"recorded_at", "valid", "validation_errors", "evidence_id"}}
            if previous_identity != identity:
                raise WorkerError("MACHINE_EVIDENCE_ID_CONFLICT")
            return self._with_validity(previous)
        _write_json(path, body)
        return self._with_validity(body)

    def _with_validity(self, record: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        try:
            _status, result = self._job_snapshot(record["request_id"])
            if result.get("tool") != record.get("tool") or result.get("action") != record.get("action"):
                errors.append("JOB_ACTION_CHANGED")
            current = self._output_manifest(result)
            if current != record.get("outputs"):
                errors.append("OUTPUT_MANIFEST_CHANGED")
        except Exception as exc:
            errors.append(getattr(exc, "code", "EVIDENCE_REVALIDATION_FAILED"))
        return {**record, "valid": not errors, "validation_errors": errors}

    def list(self, *, tool: str | None = None, action: str | None = None, limit: int = 100) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise WorkerError("INVALID_EVIDENCE_PAGE")
        records = []
        for path in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("schema_version") != SCHEMA:
                    continue
                if tool is not None and record.get("tool") != tool:
                    continue
                if action is not None and record.get("action") != action:
                    continue
                records.append(self._with_validity(record))
            except (OSError, json.JSONDecodeError):
                continue
            if len(records) >= limit:
                break
        return {"schema_version": SCHEMA, "records": records, "count": len(records)}

    def verified_actions(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for record in self.list(limit=500)["records"]:
            if record.get("valid") is True:
                key = f"{record.get('tool')}.{record.get('action')}"
                result[key] = record
        return result

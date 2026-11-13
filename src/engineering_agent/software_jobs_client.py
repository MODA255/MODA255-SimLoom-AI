"""Reconnectable controller receipts; disconnect never implies cancellation."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .transactions import _json_write, confined_path, file_hash
from .worker_jobs import ACTIVE, TERMINAL, job_id, request_digest
from .worker_protocol import SCHEMA, WorkerError, safe_file


class ReceiptStore:
    def __init__(self, project):
        self.project = Path(project).resolve()

    def path(self, identifier):
        return confined_path(self.project, ".engineering-agent/software-jobs/" + job_id(identifier) + ".json")

    def create(self, payload, bridge_url):
        identifier = payload["request_id"]
        path = self.path(identifier)
        receipt = {"schema_version": SCHEMA, "request_id": identifier,
                   "request_digest": request_digest(payload), "tool": payload["tool"], "action": payload["action"],
                   "outputs": payload["outputs"], "validation": payload.get("validation"), "bridge_url": bridge_url, "status": "submitting",
                   "before": {name: file_hash(safe_file(self.project, name)) for name in payload["outputs"]},
                   "created_at": time.time(), "updated_at": time.time(), "collected": False}
        if path.exists():
            previous = self.read(identifier)
            if previous["request_digest"] != receipt["request_digest"] or previous["bridge_url"] != bridge_url:
                raise WorkerError("LOCAL_JOB_RECEIPT_CONFLICT")
            return previous
        _json_write(path, receipt)
        return receipt

    def read(self, identifier):
        path = self.path(identifier)
        if not path.is_file():
            raise WorkerError("LOCAL_JOB_RECEIPT_MISSING", identifier)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("schema_version") != SCHEMA or receipt.get("request_id") != identifier:
            raise WorkerError("INVALID_LOCAL_JOB_RECEIPT")
        return receipt

    def update(self, identifier, **values):
        receipt = self.read(identifier)
        receipt.update(values, updated_at=time.time())
        _json_write(self.path(identifier), receipt)
        return receipt

    def stage_collected(self, identifier, transaction, output_manifest):
        receipt = self.read(identifier)
        receipt.update(collected=True, status="completed", publication_run_id=transaction.run_id,
                       output_manifest=output_manifest, updated_at=time.time())
        staged = transaction.stage_dir / "job-receipts" / (identifier + ".json")
        _json_write(staged, receipt)
        return {str(self.path(identifier).relative_to(self.project)): str(staged)}

    def list(self):
        folder = confined_path(self.project, ".engineering-agent/software-jobs")
        if not folder.is_dir():
            return []
        receipts = [self.read(path.stem) for path in folder.glob("*.json") if len(path.stem) == 32]
        return sorted(receipts, key=lambda r: r["created_at"], reverse=True)


def validate_state(body, receipt):
    if (body.get("schema_version") != SCHEMA or body.get("request_id") != receipt["request_id"]
            or body.get("request_digest") != receipt["request_digest"]
            or body.get("tool") != receipt["tool"] or body.get("action") != receipt["action"]
            or body.get("status") not in ACTIVE | TERMINAL):
        raise WorkerError("WORKER_RESPONSE_MISMATCH")


def execute_remote(adapter, payload, context, *, reconnect=False):
    if not reconnect:
        payload = adapter._prepare_remote_payload(payload, context.timeout_seconds)
    receipts = ReceiptStore(context.project_dir)
    receipt = receipts.create(payload, adapter.bridge_url)
    identifier = payload["request_id"]
    interval = max(.05, min(5, float(adapter.config.get("poll_interval_seconds", 1))))
    deadline = time.monotonic() + context.timeout_seconds
    request = ({"schema_version": SCHEMA, "operation": "job.status", "request_id": identifier}
               if reconnect else dict(payload, operation="job.submit"))
    state, last_error, previous_state = None, "", None

    def emit(status, **details):
        nonlocal previous_state
        receipts.update(identifier, status=status, **details)
        if context.on_event and status != previous_state:
            context.on_event("software_job", {"request_id": identifier, "tool": payload["tool"],
                              "action": payload["action"], "status": status, **details})
        previous_state = status

    emit("submitting")
    while time.monotonic() < deadline:
        timeout = max(.05, min(10, deadline - time.monotonic()))
        try:
            body = adapter._post(request, timeout)
            validate_state(body, receipt)
            state = body
            receipts.update(identifier, acknowledged=True)
            emit(body["status"], requires_review=body.get("requires_review", False),
                 software_lease=body.get("software_lease"))
            if body["status"] in TERMINAL:
                result = adapter._post({"schema_version": SCHEMA, "operation": "job.result", "request_id": identifier}, timeout)
                if result.get("request_digest") != receipt["request_digest"]:
                    raise WorkerError("WORKER_RESPONSE_MISMATCH")
                result["remote_job"] = {"request_id": identifier, "status": body["status"],
                                        "receipt_path": str(receipts.path(identifier)),
                                        "requires_review": body.get("requires_review", False)}
                return result
            request = {"schema_version": SCHEMA, "operation": "job.status", "request_id": identifier}
        except WorkerError as exc:
            if exc.code == "JOB_NOT_FOUND":
                if receipts.read(identifier).get("acknowledged"):
                    raise WorkerError("REMOTE_JOB_HISTORY_LOST", "Worker previously acknowledged this job; do not launch it again") from exc
                request = dict(payload, operation="job.submit")
            elif exc.code in {"JOB_NOT_FINISHED", "WORKER_FAILED"}:
                request = {"schema_version": SCHEMA, "operation": "job.status", "request_id": identifier}
            else:
                raise
            last_error = str(exc)
        except Exception as exc:
            # Request may have succeeded. Query its durable ID instead of starting a new solve.
            last_error = str(exc)
            emit("connection_lost", last_error=last_error)
            request = {"schema_version": SCHEMA, "operation": "job.status", "request_id": identifier}
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    receipts.update(identifier, status="observation_pending", last_error=last_error)
    return {"schema_version": SCHEMA, "request_id": identifier, "status": "failed", "outputs": [],
            "error": {"code": "REMOTE_JOB_PENDING", "message": "Observation ended; reconnect using this job ID. The remote job was not cancelled."},
            "remote_job": {"request_id": identifier, "status": state["status"] if state else "unknown",
                           "receipt_path": str(receipts.path(identifier)), "requires_reconnect": True,
                           "requires_review": bool((state or {}).get("requires_review"))}, "last_error": last_error}

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import PlanStep, ToolName, ToolResult
from ..software_worker import run_job
from ..software_jobs_client import ReceiptStore, execute_remote
from ..worker_protocol import (SCHEMA, MAX_HTTP_BYTES, WorkerError, decode_files, file_manifest, manifest,
                               names, pack_file, safe_file)
from ..vendor_validation import VendorValidationError, validate_report
from ..vendor_evidence import SCHEMA as EVIDENCE_SCHEMA, build_contract, requirements, strict_json, validate_evidence


class AdapterError(RuntimeError):
    pass


@dataclass
class AdapterContext:
    project_dir: Path
    dry_run: bool = True
    timeout_seconds: int = 600
    environment: dict[str, str] = field(default_factory=dict)
    workspace_dir: Path | None = None
    input_files: dict[str, str] = field(default_factory=dict)
    on_event: Any = None

    def env(self) -> dict[str, str]:
        return dict(os.environ) | self.environment


class ToolAdapter:
    name: ToolName
    capabilities: frozenset[str] = frozenset()

    def doctor(self) -> dict[str, Any]:
        return {"tool": self.name.value, "available": True, "capabilities": sorted(self.capabilities)}

    def execute(self, step: PlanStep, context: AdapterContext) -> ToolResult:
        raise NotImplementedError


class CommandAdapter(ToolAdapter):
    """Execute explicitly bound, trusted scripts via the same local/remote contract."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.executable = config.get("executable")  # Legacy config is visible, never guessed into an action.
        self.bridge_url = config.get("bridge_url")

    def _post(self, payload, timeout):
        import httpx

        token = self.config.get("bridge_token", "")
        if self.config.get("bridge_token_env"):
            token = os.environ.get(self.config["bridge_token_env"], "")
            if not token:
                raise WorkerError("BRIDGE_TOKEN_MISSING")
        headers = {"X-Bridge-Token": token} if token else {}
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_HTTP_BYTES:
            raise WorkerError("TRANSFER_TOO_LARGE")
        headers["Content-Type"] = "application/json"
        with httpx.stream("POST", self.bridge_url, content=encoded, headers=headers, timeout=timeout) as response:
            response_status = response.status_code
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_HTTP_BYTES:
                    raise WorkerError("TRANSFER_TOO_LARGE")
                chunks.append(chunk)
        body = json.loads(b"".join(chunks))
        if response_status >= 400:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise WorkerError(error.get("code", "BRIDGE_HTTP_ERROR"), error.get("message", f"HTTP {response_status}"))
        if not isinstance(body, dict) or body.get("schema_version") != SCHEMA:
            raise WorkerError("WORKER_SCHEMA_UNSUPPORTED")
        return body

    def _prepare_remote_payload(self, payload, timeout):
        """Upload large inputs in chunks, leaving small legacy requests unchanged."""
        records = payload.get("files") or []
        if not any(record.get("_source_path") and int(record.get("bytes", 0)) > 32 * 1024 * 1024
                   for record in records if isinstance(record, dict)):
            return payload
        transfer_id = payload["request_id"]
        declared = [{key: record[key] for key in ("path", "bytes", "sha256")} for record in records]
        self._post({"schema_version": SCHEMA, "operation": "artifact.upload_begin",
                    "transfer_id": transfer_id, "files": declared}, min(timeout, 30))
        chunk_size = 1024 * 1024
        for record in records:
            source = Path(record["_source_path"])
            offset = 0
            with source.open("rb") as stream:
                while True:
                    data = stream.read(chunk_size)
                    if not data:
                        break
                    import base64
                    import hashlib
                    self._post({"schema_version": SCHEMA, "operation": "artifact.upload_chunk",
                                "transfer_id": transfer_id, "path": record["path"], "offset": offset,
                                "chunk_sha256": hashlib.sha256(data).hexdigest(),
                                "content_base64": base64.b64encode(data).decode("ascii")}, min(timeout, 60))
                    offset += len(data)
        self._post({"schema_version": SCHEMA, "operation": "artifact.upload_complete",
                    "transfer_id": transfer_id}, min(timeout, 60))
        cleaned = [{key: record[key] for key in ("path", "bytes", "sha256")} for record in records]
        return {**payload, "files": cleaned, "file_transfer_id": transfer_id}

    def _download_remote_output(self, request_id, record, target, timeout):
        """Reassemble one output through bounded worker chunks and verify its hash."""
        offset = 0
        digest = hashlib.sha256()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            while offset < int(record["bytes"]):
                body = self._post({"schema_version": SCHEMA, "operation": "artifact.download_chunk",
                                   "request_id": request_id, "path": record["path"],
                                   "offset": offset, "limit": 1024 * 1024}, min(timeout, 60))
                if body.get("request_id") != request_id or body.get("path") != record["path"] or body.get("offset") != offset:
                    raise WorkerError("TRANSFER_RESPONSE_MISMATCH")
                try:
                    data = base64.b64decode(body.get("content_base64", ""), validate=True)
                except (ValueError, UnicodeError) as exc:
                    raise WorkerError("INVALID_ARTIFACT_ENCODING") from exc
                if not data or hashlib.sha256(data).hexdigest() != body.get("chunk_sha256"):
                    raise WorkerError("TRANSFER_CHUNK_HASH_MISMATCH", record["path"])
                next_offset = body.get("next_offset")
                if type(next_offset) is not int or next_offset != offset + len(data) or next_offset > int(record["bytes"]):
                    raise WorkerError("TRANSFER_RESPONSE_MISMATCH")
                stream.write(data)
                digest.update(data)
                offset = next_offset
        if offset != int(record["bytes"]):
            raise WorkerError("TRANSFER_SIZE_MISMATCH", record["path"])
        if digest.hexdigest() != record.get("sha256"):
            raise WorkerError("TRANSFER_FILE_HASH_MISMATCH", record["path"])

    def doctor(self, *, probe: bool = True) -> dict[str, Any]:
        actions = self.config.get("actions", {})
        bound = {name: cfg for name, cfg in actions.items() if isinstance(cfg, dict) and cfg.get("script")}
        item = {"tool": self.name.value, "available": False, "configured": bool(actions or self.bridge_url or self.executable),
                "reachable": False, "execution_ready": False, "real_machine_verified": False,
                "mode": "bridge" if self.bridge_url else "command", "bridge_url": self.bridge_url,
                "executable": self.executable, "capabilities": sorted(self.capabilities),
                "actions": [], "status": "unconfigured", "evidence": []}
        if item["configured"]:
            item["status"] = "configured"
        if self.bridge_url and probe:
            try:
                health = self._post({"operation": "health", "schema_version": SCHEMA}, 3)
                if health.get("ok") is not True:
                    raise WorkerError("BRIDGE_HEALTH_FAILED")
                item.update(reachable=True, status="reachable", worker_platform=health.get("platform"), worker_features=health.get("features", {}))
                checks = health.get("command_checks", {})
                item["actions"] = sorted(name for name in bound
                                         if checks.get(f"{self.name.value}.{name}", {}).get("executable_found") is True)
            except Exception as exc:
                item["evidence"].append(str(exc))
        elif not self.bridge_url:
            item["actions"] = sorted(name for name, cfg in bound.items()
                                     if self._valid_command(cfg.get("command")) and shutil.which(cfg["command"][0]))
            if item["actions"]:
                item["status"] = "executable_found"
        item["execution_ready"] = item["available"] = bool(item["actions"])
        item["evidence"].append("VENDOR_OPERATION_AND_LICENSE_NOT_VERIFIED")
        return item

    @staticmethod
    def _valid_command(command):
        return (isinstance(command, list) and bool(command)
                and all(isinstance(p, str) and p for p in command) and any("{script}" in p for p in command))

    def execute(self, step: PlanStep, context: AdapterContext) -> ToolResult:
        if context.dry_run:
            return ToolResult(step.id, self.name, "planned", f"dry-run: {self.name.value} {step.action}",
                              step.outputs, {"doctor": self.doctor(probe=False), "execution": "not_run"})
        binding = self.config.get("actions", {}).get(step.action)
        if not isinstance(binding, dict) or not binding.get("script") or (not self.bridge_url and not self._valid_command(binding.get("command"))):
            return ToolResult(step.id, self.name, "unavailable", "Configure an action script and a runner command or worker URL",
                              details={"error": {"code": "ACTION_BINDING_REQUIRED"}, "action": step.action})
        try:
            payload = self._prepare(step, context, binding)
            workspace = context.workspace_dir
            if workspace is None:
                # Raw adapter use stages results only; the agent owns publication.
                workspace = Path(tempfile.mkdtemp(prefix="simloom-software-"))
            workspace.mkdir(parents=True, exist_ok=True)
            if self.bridge_url:
                body = execute_remote(self, payload, context)
            else:
                body = run_job(workspace, payload, {f"{self.name.value}.{step.action}": binding["command"]},
                               timeout=context.timeout_seconds, environment=context.env())
            return self.collect_response(step, body, payload["request_id"], payload["outputs"], workspace,
                                         payload.get("validation"))
        except Exception as exc:
            details = {"error": {"code": getattr(exc, "code", "SOFTWARE_ADAPTER_FAILED"), "message": str(exc)}}
            if self.bridge_url and "payload" in locals():
                details["remote_job"] = {"request_id": payload["request_id"], "status": "unknown", "requires_reconnect": True,
                                         "receipt_path": str(ReceiptStore(context.project_dir).path(payload["request_id"]))}
            return ToolResult(step.id, self.name, "failed", str(exc), details=details)

    def collect_response(self, step, body, request_id, expected_outputs, workspace, validation=None):
        if body.get("schema_version") != SCHEMA or body.get("request_id") != request_id:
            raise WorkerError("WORKER_RESPONSE_MISMATCH")
        if body.get("status") == "failed":
            return ToolResult(step.id, self.name, "failed", body.get("error", {}).get("message", "Worker execution failed"),
                              details={k: v for k, v in body.items() if k != "outputs"})
        if body.get("status") != "completed" or type(body.get("returncode")) is not int or body["returncode"] != 0:
            raise WorkerError("INVALID_WORKER_COMPLETION")
        if body.get("tool") != self.name.value or body.get("action") != step.action:
            raise WorkerError("WORKER_RESPONSE_MISMATCH")
        output_records = body.get("outputs")
        if not isinstance(output_records, list):
            raise WorkerError("OUTPUT_MANIFEST_MISMATCH")
        inline_records = [record for record in output_records if "content_base64" in record]
        remote_records = [record for record in output_records if "content_base64" not in record]
        decoded = decode_files(inline_records) if inline_records else {}
        if remote_records and not self.bridge_url:
            raise WorkerError("OUTPUT_TRANSFER_REQUIRES_BRIDGE")
        if set(record.get("path") for record in output_records) != set(expected_outputs):
            raise WorkerError("OUTPUT_MANIFEST_MISMATCH")
        collected = Path(tempfile.mkdtemp(prefix="collected-", dir=workspace))
        output_files = {}
        for name, data in decoded.items():
            path = safe_file(collected, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            output_files[name] = str(path)
        for record in remote_records:
            name = record.get("path")
            if not isinstance(name, str) or type(record.get("bytes")) is not int or not isinstance(record.get("sha256"), str):
                raise WorkerError("OUTPUT_MANIFEST_MISMATCH")
            path = safe_file(collected, name)
            self._download_remote_output(request_id, record, path, 600)
            output_files[name] = str(path)
        if set(output_files) != set(expected_outputs) or any(Path(path).stat().st_size == 0 for path in output_files.values()):
            raise WorkerError("OUTPUT_MANIFEST_MISMATCH")
        details = {k: v for k, v in body.items() if k != "outputs"}
        details.update(verification="artifact_integrity", vendor_validation="not_run")
        if validation is not None:
            report_path = validation.get("report") if isinstance(validation, dict) else None
            if not isinstance(report_path, str) or report_path not in decoded:
                raise WorkerError("VENDOR_REPORT_MISSING")
            try:
                report = strict_json(Path(output_files[report_path]).read_bytes())
                if validation.get("schema_version") == EVIDENCE_SCHEMA:
                    report = validate_evidence(report, validation, decoded, request_id=request_id,
                                               tool=self.name.value, action=step.action)
                else:
                    report = validate_report(report, request_id=request_id, tool=self.name.value,
                                             action=step.action, required_checks=validation.get("required_checks", []))
            except (UnicodeDecodeError, json.JSONDecodeError, VendorValidationError) as exc:
                raise WorkerError(getattr(exc, "code", "VENDOR_REPORT_INVALID"), str(exc)) from exc
            details["vendor_validation"] = report
            details["verification"] = ("artifact_and_vendor_evidence" if validation.get("schema_version") == EVIDENCE_SCHEMA
                                       else "artifact_and_report_schema")
        details.update(output_manifest=manifest(body["outputs"]), output_files=output_files,
                       verification=details.get("verification", "artifact_integrity"),
                       vendor_validation=details.get("vendor_validation", "not_run"))
        return ToolResult(step.id, self.name, "completed", "Runner completed; declared output files collected and hashes verified",
                          list(output_files), details)

    def file_contract(self, step, binding):
        validation = binding.get("validation", step.parameters.get("validation"))
        if validation is not None and not isinstance(validation, dict):
            raise WorkerError("INVALID_VALIDATION_CONTRACT")
        typed = {("catia", "update_parameters"): "catia_parameters", ("ansys", "solve_configured_model"): "mechanical_solve"}
        kind = typed.get((self.name.value, step.action)) or (validation or {}).get("kind")
        inferred_inputs, inferred_outputs = [], []
        if kind:
            source, inferred_outputs, _ = requirements(kind, step.parameters)
            inferred_inputs = [source]
        inputs = names(list(dict.fromkeys(step.inputs + binding.get("inputs", []) + inferred_inputs)))
        outputs = names(step.outputs or binding.get("outputs", []) or inferred_outputs, output=True)
        if not outputs:
            raise WorkerError("OUTPUT_CONTRACT_REQUIRED")
        script = names([binding["script"]])[0]
        names(list(dict.fromkeys([script] + inputs)) + outputs)
        return script, inputs, outputs, validation, kind

    def _prepare(self, step, context, binding):
        script, inputs, outputs, validation, kind = self.file_contract(step, binding)
        files = []
        for name in dict.fromkeys([script] + inputs):
            if name in context.input_files:
                source = Path(context.input_files[name])
            else:
                source = context.project_dir / name
            if not source.is_file():
                record = pack_file(source.parent, source.name)
                record["path"] = name
            elif source.stat().st_size > 32 * 1024 * 1024:
                if not self.bridge_url:
                    raise WorkerError("TRANSFER_TOO_LARGE", "large local inputs require a worker bridge")
                record = file_manifest(source, name)
            else:
                record = pack_file(source.parent, source.name)
                record["path"] = name
            record["_source_path"] = str(source.resolve())
            files.append(record)
        # Small inline payloads retain the original request contract. Large
        # remote files are converted to manifests and uploaded by chunks just
        # before the durable job is registered.
        decode_files([{key: value for key, value in record.items() if key != "_source_path"}
                      for record in files if "content_base64" in record])
        names([r["path"] for r in files] + outputs)
        if kind:
            validation = build_contract(kind, step.parameters, files, outputs, self.name.value, step.action)
        elif isinstance(validation, dict):
            if validation.get("schema_version") not in (None, "simloom-ai/vendor-validation/v1"):
                raise WorkerError("VENDOR_CONTRACT_UNSUPPORTED")
            validation = {"report": validation.get("report"),
                          "required_checks": list(validation.get("required_checks", []))}
            if validation["report"] not in outputs:
                raise WorkerError("INVALID_VALIDATION_CONTRACT")
        return {"schema_version": SCHEMA, "operation": "run", "request_id": uuid.uuid4().hex,
                "tool": self.name.value, "action": step.action, "script": script,
                "parameters": step.parameters, "inputs": inputs, "outputs": outputs, "files": files,
                "validation": validation,
                **({"vendor_options": json.loads(json.dumps(binding["vendor_options"], allow_nan=False))}
                   if "vendor_options" in binding else {})}

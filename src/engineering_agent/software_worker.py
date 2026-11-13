"""Execute configured software runners in fresh, isolated job directories.

Filesystem staging is not an OS security sandbox. Runner scripts are trusted
configuration and must wait for vendor completion and export declared outputs.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import shutil
from pathlib import Path
from typing import Any

from .artifact_transfer import ArtifactTransferStore
from .worker_protocol import SCHEMA, MAX_BYTES, WorkerError, decode_files, failure, file_manifest, manifest, names, pack_file, safe_file
from .vendor_validation import VendorValidationError, read_report
from .vendor_evidence import SCHEMA as EVIDENCE_SCHEMA, check_contract, strict_json, validate_evidence, requirements


def run_job(root: Path, payload: dict[str, Any], commands: dict[str, Any], *, timeout: int = 600,
            environment: dict[str, str] | None = None, monitor=None,
            transfer_store: ArtifactTransferStore | None = None) -> dict[str, Any]:
    request_id = payload.get("request_id", "")
    try:
        return _run_job(root.resolve(), payload, commands, timeout, environment, monitor, transfer_store)
    except Exception as exc:
        return failure(exc, request_id if isinstance(request_id, str) else "")


def validate_job(payload, commands, transfer_store: ArtifactTransferStore | None = None):
    if payload.get("schema_version") != SCHEMA:
        raise WorkerError("WORKER_SCHEMA_UNSUPPORTED")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise WorkerError("INVALID_REQUEST_ID")
    tool, action = payload.get("tool"), payload.get("action")
    if tool not in {"catia", "ansys", "adams", "vi-grade"} or not isinstance(action, str):
        raise WorkerError("INVALID_SOFTWARE_ACTION")
    template = commands.get(f"{tool}.{action}")
    if not isinstance(template, list) or not template or not all(isinstance(p, str) and p for p in template):
        raise WorkerError("COMMAND_NOT_CONFIGURED", f"{tool}.{action} requires an argv list")
    if not any("{script}" in p for p in template):
        raise WorkerError("COMMAND_SCRIPT_BINDING_REQUIRED")
    inputs = names(payload.get("inputs"))
    outputs = names(payload.get("outputs"), output=True)
    if not outputs:
        raise WorkerError("OUTPUT_CONTRACT_REQUIRED")
    records = payload.get("files")
    transfer_id = payload.get("file_transfer_id")
    if transfer_id is not None:
        if transfer_store is None:
            raise WorkerError("TRANSFER_STORE_UNAVAILABLE")
        if not isinstance(records, list) or any("content_base64" in record for record in records if isinstance(record, dict)):
            raise WorkerError("INVALID_TRANSFER_MANIFEST")
        declared = [{key: record.get(key) for key in ("path", "bytes", "sha256")} for record in records]
        if declared != transfer_store.records(transfer_id):
            raise WorkerError("TRANSFER_MANIFEST_MISMATCH")
        files = {record["path"]: transfer_store.read_file(transfer_id, record["path"]) for record in declared}
    else:
        files = decode_files(records)
    script = payload.get("script")
    if script not in files or set(files) != set(inputs) | {script}:
        raise WorkerError("INPUT_MANIFEST_MISMATCH")
    names(list(files) + outputs)  # Forbid stale/in-place output and parent collisions.
    params = payload.get("parameters", {})
    if not isinstance(params, dict):
        raise WorkerError("INVALID_PARAMETERS")
    validation = payload.get("validation")
    if validation is not None:
        if not isinstance(validation, dict):
            raise WorkerError("INVALID_VALIDATION_CONTRACT")
        report = validation.get("report")
        checks = validation.get("required_checks", [])
        if validation.get("schema_version") == EVIDENCE_SCHEMA:
            expected = check_contract(validation, tool, action, outputs)
            if (validation["input_hashes"] != {r["path"]: r["sha256"] for r in payload["files"]}
                    or expected != requirements(validation["kind"], params)[2]):
                raise WorkerError("VENDOR_CONTRACT_MISMATCH")
        elif validation.get("schema_version") not in (None, "simloom-ai/vendor-validation/v1"):
            raise WorkerError("VENDOR_CONTRACT_UNSUPPORTED")
        if not isinstance(report, str) or report not in outputs or not isinstance(checks, list) or not all(isinstance(v, str) for v in checks):
            raise WorkerError("INVALID_VALIDATION_CONTRACT", "report must be one of outputs and required_checks must be a list")
    request_text = json.dumps({"schema_version": SCHEMA, "request_id": request_id,
                              "tool": tool, "action": action, "parameters": params,
                              "inputs": inputs, "outputs": outputs, "validation": validation,
                              "vendor_options": payload.get("vendor_options", {})}, ensure_ascii=False, allow_nan=False, indent=2)
    return request_id, tool, action, template, inputs, outputs, files, script, request_text


def _run_job(root, payload, commands, timeout, environment, monitor=None, transfer_store=None):
    request_id, tool, action, template, inputs, outputs, files, script, request_text = validate_job(payload, commands, transfer_store)
    job = safe_file(root, "jobs/" + request_id)
    job.parent.mkdir(parents=True, exist_ok=True)
    try:
        job.mkdir()
    except FileExistsError as exc:
        raise WorkerError("JOB_ALREADY_EXISTS", "Refusing to execute the same job twice") from exc
    request_file = job / "_simloom_request.json"
    values = {"project_dir": str(job), "script": str(job / script), "request_file": str(request_file),
              "tool": tool, "action": action}
    try:
        command = [p.format_map(values) for p in template]
    except (KeyError, ValueError) as exc:
        raise WorkerError("INVALID_COMMAND_TEMPLATE", str(exc)) from exc
    for name, data in files.items():
        target = safe_file(job, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, Path):
            with data.open("rb") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        else:
            target.write_bytes(data)
    for name in outputs:
        safe_file(job, name).parent.mkdir(parents=True, exist_ok=True)
    request_file.write_text(request_text, encoding="utf-8")
    logs = job / "_logs"
    logs.mkdir()
    details = {"schema_version": SCHEMA, "request_id": request_id, "tool": tool, "action": action,
               "job_dir": str(job), "command": command, "inputs": manifest(payload["files"]),
               "verification": "artifact_integrity", "vendor_validation": "not_run"}
    process = None
    try:
        with (logs / "stdout.txt").open("wb") as stdout, (logs / "stderr.txt").open("wb") as stderr:
            if monitor and monitor(None):
                raise WorkerError("JOB_CANCELLED", "Cancelled before runner launch")
            process = subprocess.Popen(command, cwd=job, env=environment, stdout=stdout, stderr=stderr,
                                       stdin=subprocess.DEVNULL, shell=False, close_fds=True)
            if monitor:
                monitor(process.pid)
            started = time.monotonic()
            while process.poll() is None:
                cancel = monitor(process.pid) if monitor else False
                if cancel or time.monotonic() - started >= timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise WorkerError("JOB_CANCELLED" if cancel else "SOFTWARE_TIMEOUT",
                                      "Runner stopped; vendor session state requires inspection")
                time.sleep(0.1)
            details["returncode"] = process.returncode
    except WorkerError as exc:
        details.update(failure(exc, request_id), requires_review=process is not None)
    except OSError as exc:
        details.update(failure(WorkerError("SOFTWARE_START_FAILED", str(exc)), request_id), requires_review=process is not None)
    except Exception:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise
    for name in ("stdout", "stderr"):
        with (logs / f"{name}.txt").open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 10000))
            details[name] = stream.read().decode("utf-8", errors="replace")
    if details.get("status") == "failed":
        return details
    if details["returncode"] != 0:
        return details | failure(WorkerError("SOFTWARE_EXIT_FAILED", f"Runner exited with {details['returncode']}"), request_id)
    try:
        records, total = [], 0
        for name in outputs:
            target = safe_file(job, name)
            if not target.is_file():
                raise WorkerError("ARTIFACT_MISSING", name)
            record = (pack_file(job, name) if target.stat().st_size <= MAX_BYTES
                      else file_manifest(target, name) | {"transfer_required": True})
            if record["bytes"] == 0:
                raise WorkerError("OUTPUT_EMPTY", name)
            total += record["bytes"]
            records.append(record)
        if total > MAX_BYTES:
            records = [file_manifest(safe_file(job, name), name) | {"transfer_required": True} for name in outputs]
    except WorkerError as exc:
        return details | failure(exc, request_id)
    validation = payload.get("validation")
    if validation is not None:
        try:
            if validation.get("schema_version") == EVIDENCE_SCHEMA:
                if any(pack_file(job, r["path"])["sha256"] != r["sha256"] for r in payload["files"]):
                    raise WorkerError("VENDOR_INPUT_CHANGED")
                decoded = decode_files(records)
                report = validate_evidence(strict_json(decoded[validation["report"]]), validation, decoded,
                                           request_id=request_id, tool=tool, action=action)
            else:
                report = read_report(job, validation["report"], request_id=request_id, tool=tool, action=action,
                                     required_checks=validation.get("required_checks", []))
        except (VendorValidationError, WorkerError) as exc:
            return details | failure(exc, request_id) | {"vendor_validation": "failed"}
        details["verification"] = ("artifact_and_vendor_evidence" if validation.get("schema_version") == EVIDENCE_SCHEMA
                                   else "artifact_and_report_schema")
        details["vendor_validation"] = report
    return details | {"status": "completed", "outputs": records}

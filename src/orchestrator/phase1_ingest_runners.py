"""Phase-one ingestion runners with one observable contract for every source."""
import json
import time
from pathlib import Path

from .material_ingest import ingest_and_wait_ready, MaterialIngestError, _asset_dict
from .live_ingest import run_live_ingest as _legacy_live_ingest
from .processing_history import append_processing_history

CONTRACT_KEYS = ("preflight", "submit", "poll", "accept", "resources")
DEFAULT_UPLOAD_TIMEOUT = 1800


def _infer_source_type(spec, source):
    explicit = spec.get("source_type") or (spec.get("payload") or {}).get("source_type")
    if explicit:
        value = str(explicit).strip().lower()
        if value in {"master_tape", "live_recording"}:
            return value
        if value in {"douyin_video", "weibo_video", "video", "link", "master"}:
            return "master_tape"
        if value in {"douyin_live", "live", "recording"}:
            return "live_recording"
    chain_id = str(spec.get("chain_id", "")).upper()
    if chain_id == "I-LINK" or source == "link":
        return "master_tape"
    if chain_id == "I-LIVE" or source == "live":
        return "live_recording"
    return None


def _build_ingest_payload(spec, source):
    raw = spec.get("payload") or {}
    url = spec.get("source_url") or spec.get("input") or raw.get("url") or next(iter(spec.get("source_urls") or []), None)
    source_type = spec.get("source_type") or raw.get("source_type") or _infer_source_type(spec, source)
    folder_id = spec.get("folder_id") or spec.get("folder") or raw.get("folder_id")
    if not url or not source_type or (source == "link" and not folder_id):
        raise ValueError("INVALID_INPUT: url, source_type and folder_id are required")
    payload = {"url": url, "source_type": source_type, "folder_id": folder_id}
    return payload


def _persist(result, spec, output_dir):
    directory = Path(output_dir) / spec.get("chain_id", "ingest")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ingest-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def _base(spec, source):
    return {"chain_id": spec.get("chain_id", f"ingest_{source}"), "source": source,
            "preflight": {"status": "passed"}, "submit": {"status": "not_started"},
            "poll": {"status": "not_started"}, "accept": {"status": "inconclusive"},
            "resources": [], "evidence": [], "downstream_ready": False}


def _rid(value):
    return (value.get("id") or value.get("asset_id") or value.get("resource_id")) if isinstance(value, dict) else None


def _reserve(result, spec):
    registry = spec.get("resource_registry")
    if registry is None or not spec.get("case_run_id"):
        return None
    try:
        record = registry.register_pending(spec.get("run_id", "phase1"), spec.get("case_id", result["chain_id"]), spec.get("resource_type", result["source"]), case_run_id=spec["case_run_id"], operation_key=spec.get("operation_key"), payload=spec.get("payload"))
        result["resources"] = [record]
        return record
    except Exception as exc:
        result["evidence"].append({"operation": "resource_registry", "status": "failed", "error": type(exc).__name__, "message": str(exc)})
        return None


def _protect_pending(result, spec, record, reason):
    if record is None:
        return
    try:
        registry = spec["resource_registry"]
        result["resources"] = [registry.repository.update_resource(record["id"], {
            "status": "submission_unknown", "protected": True,
            "protection_reason": reason or "submission outcome unknown",
        })]
    except Exception as exc:
        result["evidence"].append({"operation": "resource_registry", "status": "failed", "error": type(exc).__name__, "message": str(exc)})


def _register(result, spec, record, resource_id):
    if record is None:
        return True
    try:
        result["resources"] = [spec["resource_registry"].mark_submitted(record["id"], str(resource_id))]
        return True
    except Exception as exc:
        result["evidence"].append({"operation": "resource_registry", "status": "failed", "error": type(exc).__name__, "message": str(exc)})
        result.update(accept={"status": "inconclusive", "code": "RESOURCE_REGISTRY_FAILED"}, test_status="inconclusive", execution_status="inconclusive", downstream_ready=False)
        return False


def _valid_timing(spec):
    timeout = spec.get("timeout_seconds", 1800)
    poll = spec.get("poll_interval_seconds", 20)
    return (
        not isinstance(timeout, bool) and not isinstance(poll, bool)
        and isinstance(timeout, (int, float)) and isinstance(poll, (int, float))
        and timeout >= 0 and poll >= 0
    )


def run_link_ingest(adapter, spec, output_dir):
    result = _base(spec, "link")
    result["preflight"] = {"status": "passed", "folder_id": spec.get("folder_id")}
    pending = _reserve(result, spec)
    if not _valid_timing(spec):
        result.update(
            submit={"status": "blocked", "accepted": False},
            poll={"status": "blocked", "code": "INVALID_INPUT"},
            accept={"status": "blocked", "code": "INVALID_INPUT"},
            failure={"code": "INVALID_INPUT", "message": "nonnegative timeout/poll required"},
            test_status="blocked", execution_status="blocked",
        )
        _protect_pending(result, spec, pending, "INVALID_INPUT")
        return _persist(result, spec, output_dir)
    try:
        payload = _build_ingest_payload(spec, "link")
        operation_store = spec.get("operation_store")
        if operation_store is None and spec.get("resource_registry") is not None:
            from .daily import PersistentOperations
            operation_store = PersistentOperations(spec["resource_registry"].repository)
        def progress_callback(snapshot):
            result["progress"] = snapshot
            result.setdefault("progress_history", []).append(snapshot)
            if snapshot.get("step") == "asset_status":
                append_processing_history(result, status=snapshot.get("status", "processing"), asset=snapshot.get("asset"), progress=snapshot.get("progress"), operation="asset_status")
            _persist(result, spec, output_dir)
        outcome = ingest_and_wait_ready(adapter, payload, spec.get("folder_id") or spec.get("folder") or payload.get("folder_id"), source_url=payload["url"], title=None, timeout=spec.get("timeout_seconds", 1800), poll=spec.get("poll_interval_seconds", 20), operation_key=spec.get("operation_key"), operation_store=operation_store, progress_callback=progress_callback)
        rid = _rid(outcome.get("asset", {}))
        result["submit"] = {"status": "accepted", "accepted": True, "resource_id": rid, "evidence": outcome.get("evidence", [])}
        result["poll"] = {"status": "ready", "ingest": outcome}; result["accept"] = {"status": "passed"}
        result["test_status"] = "passed"; result["execution_status"] = "completed"; result["evidence"].extend(outcome.get("evidence", []))
        result["downstream_ready"] = bool(rid and str(result["poll"].get("status", "")).lower() in {"ready", "passed", "completed"} and _register(result, spec, pending, rid))
    except (MaterialIngestError, ValueError) as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        detail = getattr(exc, "detail", str(exc))
        status = "inconclusive" if code in {"ASSET_TIMEOUT", "SUBMISSION_UNKNOWN", "NO_UNIQUE_MATCH"} else "blocked" if code in {"MISSING_QUERY_ID", "AMBIGUOUS_MATCH", "INVALID_INPUT"} else "failed"
        submit_status = "unknown" if code in {"ASSET_TIMEOUT", "SUBMISSION_UNKNOWN"} else "accepted" if code == "NO_UNIQUE_MATCH" else "failed"
        evidence = getattr(exc, "evidence", None) or [{"operation": "ingest_url", "status": "blocked", "error": code, "message": detail}]
        result.update(submit={"status": submit_status, "accepted": False, "evidence": evidence}, poll={"status": status}, accept={"status": status, "code": code}, failure={"code": code, "message": detail}, evidence=evidence, test_status=status, execution_status=status)
        if code in {"ASSET_TIMEOUT", "SUBMISSION_UNKNOWN", "NO_UNIQUE_MATCH"}:
            _protect_pending(result, spec, pending, code)
    return _persist(result, spec, output_dir)


def run_live_ingest(adapter, spec, output_dir):
    result = _base(spec, "live"); pending = _reserve(result, spec)
    try:
        payload = _build_ingest_payload(spec, "live")
    except ValueError as exc:
        result.update(preflight={"status": "blocked"}, submit={"status": "blocked", "accepted": False}, poll={"status": "blocked", "code": "INVALID_INPUT"}, accept={"status": "blocked", "code": "INVALID_INPUT"}, failure={"code": "INVALID_INPUT", "message": str(exc)}, test_status="blocked", execution_status="blocked")
        return _persist(result, spec, output_dir)
    spec = {**spec, "payload": payload, "source_type": "live_recording"}
    legacy = _legacy_live_ingest(adapter, spec, output_dir); result.update(legacy)
    legacy_status = legacy.get("test_status")
    passed = legacy_status == "passed"
    result["preflight"] = {"status": "passed" if legacy.get("source_probe", {}).get("status") == "passed" else "blocked"}
    if passed:
        result["submit"] = {"status": "accepted", "accepted": True}
        result["poll"] = {"status": "ready"}; result["accept"] = {"status": "passed"}
    elif legacy_status in {"failed", "unknown", "timeout", "timed_out", "asset_failed"}:
        if legacy_status in {"timeout", "timed_out"}:
            legacy["test_status"] = "inconclusive"
            legacy["execution_status"] = "inconclusive"
            legacy.setdefault("failure", {"code": "ASSET_TIMEOUT", "message": "live ingest timed out"})
        submit_status = "accepted" if legacy_status == "asset_failed" and (legacy.get("ingest") or {}).get("asset") else "failed" if legacy_status == "failed" else "unknown"
        result["submit"] = {"status": submit_status, "accepted": submit_status == "accepted", "evidence": legacy.get("evidence", [])}
        result["poll"] = {"status": "inconclusive" if legacy_status in {"timeout", "timed_out"} else legacy_status}; result["accept"] = {"status": "failed" if legacy_status in {"failed", "asset_failed"} else "inconclusive"}
        result.setdefault("evidence", []).extend(legacy.get("evidence", []))
        _protect_pending(result, spec, pending, legacy.get("failure", {}).get("code", legacy_status))
    else:
        result["submit"] = {"status": "not_started", "accepted": False}
        result["poll"] = {"status": "not_started"}; result["accept"] = {"status": "blocked"}
    rid = _rid((legacy.get("ingest") or {}).get("asset", {}))
    if rid: result["submit"].update(resource_id=rid, evidence=(legacy.get("ingest") or {}).get("evidence", []))
    result["downstream_ready"] = bool(passed and rid and str(result["poll"].get("status", "")).lower() in {"ready", "passed", "completed"} and _register(result, spec, pending, rid))
    return _persist(result, spec, output_dir)


def run_upload_ingest(adapter, spec, output_dir):
    result = _base(spec, "upload"); pending = _reserve(result, spec)
    timeout = spec.get("timeout_seconds", DEFAULT_UPLOAD_TIMEOUT); interval = spec.get("poll_interval_seconds", 20)
    if isinstance(timeout, bool) or isinstance(interval, bool) or not isinstance(timeout, (int, float)) or not isinstance(interval, (int, float)) or timeout < 0 or interval < 0:
        result.update(submit={"status": "blocked", "accepted": False}, poll={"status": "blocked", "code": "INVALID_INPUT"}, accept={"status": "blocked", "code": "INVALID_INPUT"}, failure={"code": "INVALID_INPUT", "message": "nonnegative timeout/poll required"}, test_status="blocked", execution_status="blocked")
        _protect_pending(result, spec, pending, "INVALID_INPUT")
        return _persist(result, spec, output_dir)
    payload = spec.get("payload") or {}
    # A previously registered asset is safe to observe again.  An explicit
    # asset ID is also supported for recovery of a prior run whose evidence
    # was copied into the current run.  Neither path performs another upload.
    rid = spec.get("existing_asset_id") or spec.get("asset_id") or payload.get("asset_id")
    resumed = bool(rid)
    if pending and pending.get("resource_id"):
        rid = pending["resource_id"]
        resumed = True
    if pending and pending.get("status") == "submission_unknown" and not rid:
        result.update(
            submit={"status": "unknown", "accepted": False},
            poll={"status": "inconclusive", "code": "RECONCILIATION_REQUIRED"},
            accept={"status": "inconclusive", "code": "RECONCILIATION_REQUIRED"},
            test_status="inconclusive", execution_status="inconclusive",
            failure={"code": "RECONCILIATION_REQUIRED", "message": "submission_unknown asset must be reconciled before upload retry"},
        )
        result["evidence"].append({"operation": "upload", "status": "reconciliation_required", "resource_id": None})
        return _persist(result, spec, output_dir)
    if not resumed and not callable(getattr(adapter, "ingest_file", None)):
        result.update(submit={"status": "blocked", "accepted": False}, accept={"status": "blocked", "code": "ADAPTER_CAPABILITY_MISSING"}, test_status="blocked", execution_status="blocked", failure={"code": "ADAPTER_CAPABILITY_MISSING", "message": "ingest_file adapter is required"})
        return _persist(result, spec, output_dir)
    try:
        if resumed:
            rid = str(rid)
            submitted = {"id": rid, "status": "resume_observation"}
            result["submit"] = {"status": "reconciled", "accepted": True, "response": submitted, "resource_id": rid, "resumed": True}
            result["evidence"].append({"operation": "upload", "status": "resumed_without_repost", "resource_id": rid})
        else:
            submitted = _asset_dict(adapter.ingest_file(payload))
            if not submitted: raise TypeError("submission response must contain an asset object")
            rid = _rid(submitted); result["submit"] = {"status": "accepted", "accepted": True, "response": submitted, "resource_id": rid}
    except Exception as exc:
        result.update(submit={"status": "failed", "accepted": False}, accept={"status": "blocked"}, test_status="blocked", execution_status="blocked", failure={"code": type(exc).__name__, "message": str(exc)})
        _protect_pending(result, spec, pending, type(exc).__name__)
        return _persist(result, spec, output_dir)
    if not rid:
        result.update(poll={"status": "blocked", "code": "MISSING_QUERY_ID"}, accept={"status": "blocked", "code": "MISSING_QUERY_ID"}, test_status="blocked", execution_status="blocked")
        _protect_pending(result, spec, pending, "MISSING_QUERY_ID")
        return _persist(result, spec, output_dir)
    started = time.monotonic(); timeout = spec.get("timeout_seconds", DEFAULT_UPLOAD_TIMEOUT); interval = spec.get("poll_interval_seconds", 20)
    while True:
        try:
            asset = _asset_dict(adapter.get_asset(rid), rid) if hasattr(adapter, "get_asset") else submitted
            progress = _asset_dict(adapter.get_asset_progress(rid), rid) if hasattr(adapter, "get_asset_progress") else asset
            if not asset or not progress: raise TypeError("query response must contain asset objects")
            result["evidence"].append({"operation": "asset_query", "asset": asset, "progress": progress})
            states = {str(x).lower() for x in (asset.get("status"), progress.get("status"), progress.get("state")) if x}
            priority = ("failed", "error", "rejected", "ready", "active", "processing", "pending")
            status = next((candidate for candidate in priority if candidate in states), "processing")
            append_processing_history(result, status=status, asset=asset, progress=progress)
        except Exception as exc:
            result["evidence"].append({"operation": "asset_query", "status": "failed", "error": type(exc).__name__, "message": str(exc)})
            result.update(poll={"status": "inconclusive"}, accept={"status": "inconclusive", "code": "QUERY_FAILED"}, test_status="inconclusive", execution_status="inconclusive")
            _protect_pending(result, spec, pending, "QUERY_FAILED")
            break
        if states & {"ready", "active"}:
            result.update(poll={"status": "ready", "asset": asset, "progress": progress}, accept={"status": "passed"}, test_status="completed", execution_status="completed")
            result["downstream_ready"] = _register(result, spec, pending, rid); break
        if states & {"failed", "error", "rejected"}:
            result.update(poll={"status": "failed", "asset": asset}, accept={"status": "failed"}, test_status="failed", execution_status="failed")
            _protect_pending(result, spec, pending, "ASSET_FAILED")
            break
        if time.monotonic() - started >= timeout:
            result.update(poll={"status": "inconclusive", "asset": asset, "progress": progress}, accept={"status": "inconclusive", "code": "NOT_READY"}, test_status="inconclusive", execution_status="inconclusive")
            _protect_pending(result, spec, pending, "ASSET_TIMEOUT")
            break
        time.sleep(min(interval, max(timeout - (time.monotonic() - started), 0.01)))
    return _persist(result, spec, output_dir)

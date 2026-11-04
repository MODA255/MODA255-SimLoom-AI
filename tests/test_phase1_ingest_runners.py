import json

from orchestrator.phase1_ingest_runners import (
    run_link_ingest,
    run_live_ingest,
    run_upload_ingest,
)


class LinkAdapter:
    def ingest_url(self, payload): return {"id": "link-1"}
    def get_asset(self, asset_id): return {"id": asset_id, "status": "ready", "source_url": "https://x"}
    def get_asset_progress(self, asset_id): return {"status": "ready", "percent": 100}


def test_link_runner_exposes_uniform_contract_and_reuses_material_ingest(tmp_path):
    result = run_link_ingest(LinkAdapter(), {
        "chain_id": "i-link", "folder_id": "f", "source_url": "https://x",
        "payload": {}, "timeout_seconds": 0, "poll_interval_seconds": 0,
    }, tmp_path)
    assert set(("preflight", "submit", "poll", "accept", "resources")) <= set(result)
    assert result["accept"]["status"] == "passed"
    assert result["test_status"] == "passed"


def test_link_runner_persistent_store_deduplicates_and_rejects_conflict(tmp_path):
    from orchestrator.repository import Repository
    from orchestrator.daily import PersistentOperations
    from orchestrator.material_ingest import MaterialIngestError
    calls = []
    class Adapter(LinkAdapter):
        def ingest_url(self, payload): calls.append(payload); return {"id": "link-1"}
    store = PersistentOperations(Repository(tmp_path / "state.db"))
    spec = {"chain_id": "i-link", "folder_id": "f", "source_url": "https://x", "operation_key": "op-1", "payload": {}, "timeout_seconds": 0, "poll_interval_seconds": 0, "operation_store": store}
    run_link_ingest(Adapter(), spec, tmp_path)
    run_link_ingest(Adapter(), spec, tmp_path)
    assert len(calls) == 1
    result = run_link_ingest(Adapter(), {**spec, "source_url": "https://other"}, tmp_path)
    assert result["failure"]["code"] == "OPERATION_KEY_CONFLICT"


def test_link_runner_reconciles_legacy_submission_unknown_without_reposting(tmp_path):
    from orchestrator.repository import Repository
    from orchestrator.daily import PersistentOperations
    repo = Repository(tmp_path / "state.db")
    store = PersistentOperations(repo)
    repo.operation("legacy-op", "submission_unknown")
    calls = []

    class Adapter(LinkAdapter):
        def ingest_url(self, payload):
            calls.append(payload)
            raise AssertionError("legacy unknown must reconcile without reposting")
        def list_assets(self, folder_id):
            return [{"id": "legacy-asset", "folder_id": folder_id, "source_url": "https://x"}]
        def get_asset(self, asset_id):
            return {"id": asset_id, "status": "ready", "source_url": "https://x"}
        def get_asset_progress(self, asset_id):
            return {"status": "ready", "percent": 100}

    result = run_link_ingest(Adapter(), {
        "chain_id": "legacy", "folder_id": "f", "source_url": "https://x",
        "operation_key": "legacy-op", "payload": {}, "timeout_seconds": 0,
        "poll_interval_seconds": 0, "operation_store": store,
    }, tmp_path)
    assert result["test_status"] == "passed"
    assert result["submit"]["resource_id"] == "legacy-asset"
    assert calls == []


def test_processing_history_status_is_independent_of_set_hash_order(tmp_path):
    from orchestrator.phase1_ingest_runners import run_upload_ingest

    class Adapter:
        def ingest_file(self, payload): return {"id": "u1", "status": "pending"}
        def get_asset(self, resource_id): return {"id": resource_id, "status": "ready"}
        def get_asset_progress(self, resource_id): return {"status": "pending", "state": "active", "percent": 80}

    result = run_upload_ingest(Adapter(), {"chain_id": "history", "payload": {}, "timeout_seconds": 0, "poll_interval_seconds": 0}, tmp_path)
    assert result["processing_history"][0]["status"] == "ready"


def test_live_runner_uses_existing_live_probe_and_uniform_contract(tmp_path):
    result = run_live_ingest(object(), {
        "chain_id": "i-live", "source_urls": ["bad"], "folder_id": "f",
    }, tmp_path)
    assert set(("preflight", "submit", "poll", "accept", "resources")) <= set(result)
    assert result["accept"]["status"] == "blocked"
    assert result["test_status"] == "blocked"


class UploadPendingAdapter:
    def ingest_file(self, payload): return {"id": "upload-1", "status": "pending"}
    def get_asset(self, resource_id): return {"id": resource_id, "status": "pending"}
    def get_asset_progress(self, resource_id): return {"status": "pending", "percent": 20}


def test_upload_pending_cannot_complete_or_drive_downstream(tmp_path):
    result = run_upload_ingest(UploadPendingAdapter(), {
        "chain_id": "i-upload", "payload": {"path": "x"},
        "timeout_seconds": 0, "poll_interval_seconds": 0,
    }, tmp_path)
    assert result["accept"]["status"] in {"inconclusive", "blocked"}
    assert result["test_status"] != "passed"
    assert result["downstream_ready"] is False


def test_upload_without_remote_query_id_is_blocked_and_persisted(tmp_path):
    class Adapter:
        def ingest_file(self, payload): return {"status": "accepted"}
    result = run_upload_ingest(Adapter(), {
        "chain_id": "i-upload", "payload": {"path": "x"},
    }, tmp_path)
    assert result["accept"]["status"] == "blocked"
    assert result["execution_status"] == "blocked"
    assert result["downstream_ready"] is False
    saved = json.loads((tmp_path / "i-upload" / "ingest-result.json").read_text())
    assert saved["resources"] == []


def test_upload_uses_nonzero_injectable_default_timeout_and_query_evidence(tmp_path):
    class Adapter:
        def ingest_file(self, payload): return {"id": "u1", "status": "pending"}
        def get_asset(self, resource_id): raise RuntimeError("query down")
    result = run_upload_ingest(Adapter(), {"chain_id": "q", "payload": {}}, tmp_path)
    assert result["accept"]["status"] == "inconclusive"
    assert result["execution_status"] == "inconclusive"
    assert result["evidence"][0]["operation"] == "asset_query"
    assert result["evidence"][0]["error"] == "RuntimeError"


def test_resource_registry_is_pending_before_write_and_live_success_registers(tmp_path, monkeypatch):
    from orchestrator.repository import Repository
    from orchestrator.resource_registry import ResourceRegistry

    monkeypatch.setattr("orchestrator.live_ingest.choose_live_source", lambda urls, search=None: {"status": "passed", "selected_url": urls[0], "attempts": []})
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    class Adapter:
        def ingest_url(self, payload): return {"id": "live-1"}
        def get_asset(self, asset_id): return {"id": asset_id, "status": "ready"}
        def get_asset_progress(self, asset_id): return {"status": "ready"}
    result = run_live_ingest(Adapter(), {
        "chain_id": "live-ok", "source_urls": ["https://x"], "folder_id": "f",
        "case_run_id": "cr", "resource_registry": registry,
    }, tmp_path)
    assert result["test_status"] == "passed"
    assert result["resources"][0]["status"] == "registered"
    assert result["resources"][0]["resource_id"] == "live-1"


def test_link_submit_contains_accepted_and_resource_evidence(tmp_path):
    result = run_link_ingest(LinkAdapter(), {
        "chain_id": "link-evidence", "folder_id": "f", "source_url": "https://x",
        "payload": {}, "timeout_seconds": 0, "poll_interval_seconds": 0,
    }, tmp_path)
    assert result["submit"]["accepted"] is True
    assert result["submit"]["resource_id"] == "link-1"
    assert result["submit"]["evidence"]


def test_http_status_error_details_are_preserved_in_link_evidence(tmp_path):
    import httpx
    import orchestrator.adapters as adapters
    from orchestrator.adapters import RemoteAdapter
    request = httpx.Request("POST", "https://example.test/api/v1/assets/import-url")
    response = httpx.Response(422, json={"code": "INVALID_URL", "message": "unsupported"}, request=request)
    adapter = RemoteAdapter({"target": {"base_url": "https://example.test"}, "remote_api": {"routes": {"import_url": "POST /assets/import-url"}}})
    original = adapters.httpx.request
    adapters.httpx.request = lambda *args, **kwargs: response
    try:
        result = run_link_ingest(adapter, {"chain_id": "link-http", "folder_id": "f", "source_url": "https://x"}, tmp_path)
    finally:
        adapters.httpx.request = original
    evidence = result["evidence"][-1]
    assert evidence["status"] == 422
    assert evidence["url"] == "https://example.test/api/v1/assets/import-url"
    assert evidence["detail"] == {"code": "INVALID_URL", "message": "unsupported"}



    class Adapter:
        def ingest_file(self, payload):
            raise AssertionError("must reject before submission")
    result = run_upload_ingest(Adapter(), {
        "chain_id": "bad-upload", "payload": {}, "timeout_seconds": -1, "poll_interval_seconds": -2,
    }, tmp_path)
    assert result["submit"]["status"] == "blocked"
    assert result["poll"]["status"] == "blocked"
    assert result["failure"]["code"] == "INVALID_INPUT"
    assert result["downstream_ready"] is False


def test_live_legacy_failure_preserves_failure_and_protects_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.phase1_ingest_runners._legacy_live_ingest", lambda *args: {
        "chain_id": "live-fail", "execution_status": "failed", "test_status": "failed",
        "source_probe": {"status": "passed"}, "failure": {"code": "ASSET_FAILED", "message": "bad"},
        "evidence": [{"operation": "asset_status", "status": "failed"}],
    })
    result = run_live_ingest(object(), {"chain_id": "live-fail", "source_urls": ["x"], "case_run_id": "cr", "resource_registry": _registry(tmp_path)}, tmp_path)
    assert result["test_status"] == "failed"
    assert result["accept"]["status"] == "failed"
    assert result["failure"]["code"] == "ASSET_FAILED"
    assert result["evidence"]
    assert result["resources"][0]["status"] == "submission_unknown"
    assert result["resources"][0]["protected"] is True


def _registry(tmp_path):
    from orchestrator.repository import Repository
    from orchestrator.resource_registry import ResourceRegistry
    return ResourceRegistry(Repository(tmp_path / "state.db"))


def test_link_error_keeps_submit_evidence_and_unknown_is_protected(tmp_path):
    class Adapter:
        def ingest_url(self, payload):
            raise RuntimeError("network down")
    result = run_link_ingest(Adapter(), {"chain_id": "link-fail", "folder_id": "f", "source_url": "https://x", "case_run_id": "cr", "resource_registry": _registry(tmp_path)}, tmp_path)
    assert result["submit"]["status"] == "unknown"
    assert result["submit"]["evidence"]
    assert result["accept"]["status"] == "inconclusive"
    assert result["resources"][0]["status"] == "submission_unknown"
    assert result["resources"][0]["protected"] is True


def test_link_negative_timeout_poll_is_structured_without_calling_adapter(tmp_path):
    class Adapter:
        def ingest_url(self, payload):
            raise AssertionError("must reject invalid timing before submission")
    result = run_link_ingest(Adapter(), {
        "chain_id": "bad-link", "folder_id": "f", "source_url": "https://x",
        "timeout_seconds": -1, "poll_interval_seconds": -2,
    }, tmp_path)
    assert result["submit"]["status"] == "blocked"
    assert result["poll"] == {"status": "blocked", "code": "INVALID_INPUT"}
    assert result["failure"]["code"] == "INVALID_INPUT"


def test_live_status_mapping_preserves_failure_and_protects_pending(tmp_path, monkeypatch):
    for status, submit_status, accept_status in (
        ("failed", "failed", "failed"),
        ("unknown", "unknown", "inconclusive"),
        ("timeout", "unknown", "inconclusive"),
        ("asset_failed", "accepted", "failed"),
    ):
        monkeypatch.setattr("orchestrator.phase1_ingest_runners._legacy_live_ingest", lambda *args, status=status: {
            "chain_id": "live-" + status, "execution_status": status, "test_status": status,
            "source_probe": {"status": "passed"},
            "failure": {"code": "ORIGINAL", "message": "keep me"},
            "evidence": [{"operation": "live", "status": status}],
            "ingest": {"asset": {"id": "live-asset"}} if status == "asset_failed" else {},
        })
        result = run_live_ingest(object(), {
            "chain_id": "live-" + status, "source_urls": ["x"], "case_run_id": "cr",
            "resource_registry": _registry(tmp_path),
        }, tmp_path)
        assert result["submit"]["status"] == submit_status
        assert result["accept"]["status"] == accept_status
        assert result["failure"]["code"] == "ORIGINAL"
        assert result["resources"][0]["status"] == "submission_unknown"
        assert result["resources"][0]["protected"] is True

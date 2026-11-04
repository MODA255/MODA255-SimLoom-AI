from orchestrator.adapters import RemoteAdapter


def test_remote_adapter_exposes_unified_evidence_route():
    adapter = RemoteAdapter({"target":{"base_url":"https://example.com","api_prefix":"/api/v1"},"remote_api":{"routes":{"get_vfc_evidence":"GET /vfc/tasks/{task_id}/evidence"}},"auth":{}})
    assert adapter._route("get_vfc_evidence") == ("GET", "/vfc/tasks/{task_id}/evidence")
    assert callable(adapter.get_evidence)

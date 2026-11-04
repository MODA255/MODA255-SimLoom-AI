import json
from pathlib import Path

from orchestrator.cli import _merge_phase1_remote_references


def test_phase1_cli_merges_remote_reference_sections_with_local_precedence(tmp_path):
    (tmp_path / "remote-test.local.json").write_text(json.dumps({
        "auth": {"type": "bearer", "token_env": "REMOTE_TOKEN"},
        "remote_api": {"routes": {
            "vfc_config": "GET /remote-config",
            "asset_detail": "GET /assets/{asset_id}",
        }},
        "automation_routes": {"get_task": "GET /automation/{task_id}"},
        "cleanup_routes": {"asset": "DELETE /assets/{id}"},
    }))
    local = {
        "auth": {"token_env": "LOCAL_TOKEN"},
        "remote_api": {"routes": {"vfc_config": "GET /local-config"}},
    }

    merged = _merge_phase1_remote_references(local, tmp_path / "phase1.json")

    assert merged["auth"]["token_env"] == "LOCAL_TOKEN"
    assert merged["remote_api"]["routes"]["vfc_config"] == "GET /local-config"
    assert merged["remote_api"]["routes"]["asset_detail"] == "GET /assets/{asset_id}"
    assert merged["automation_routes"]["get_task"] == "GET /automation/{task_id}"
    assert merged["cleanup_routes"]["asset"] == "DELETE /assets/{id}"


def test_phase1_config_merge_is_local_only_and_does_not_write_or_call_remote(tmp_path):
    remote = tmp_path / "remote-test.local.json"
    remote.write_text(json.dumps({"remote_api": {"routes": {"vfc_config": "GET /x"}}}))
    local = {"remote_api": {"routes": {"vfc_config": "GET /local"}}}

    merged = _merge_phase1_remote_references(local, tmp_path / "phase1.json")

    assert merged["remote_api"]["routes"]["vfc_config"] == "GET /local"
    assert remote.read_text()
    assert list(tmp_path.iterdir()) == [remote]

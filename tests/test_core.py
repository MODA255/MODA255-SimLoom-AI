import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import load_config, ConfigError
from orchestrator.repository import Repository
from orchestrator.engine import Orchestrator, SubmissionUnknown
from orchestrator.policy import sanitize, validate_dynamic_payload


def config(tmp_path, mode="simulation"):
    data = json.loads((ROOT / "configs" / "example.json").read_text())
    data["mode"] = mode
    data["state_db"] = str(tmp_path / "state.db")
    return data


def test_config_rejects_secret_and_unknown_mode(tmp_path):
    data = config(tmp_path)
    data["auth"]["token"] = "secret"
    with pytest.raises(ConfigError):
        load_config(data)
    data = config(tmp_path)
    data["mode"] = "invalid"
    with pytest.raises(ConfigError):
        load_config(data)


def test_config_snapshot_immutable(tmp_path):
    c = load_config(config(tmp_path))
    repo = Repository(c.state_db)
    run_id = repo.create_run(c.snapshot())
    with pytest.raises(ConfigError):
        repo.assert_snapshot(run_id, {**c.snapshot(), "profiles": {"publish": True}})


def test_partial_failure_keeps_healthy_branch(tmp_path):
    c = load_config(config(tmp_path))
    repo = Repository(c.state_db)
    orch = Orchestrator(c, repo)
    result = orch.run()
    assert result["result"] == "blocked"
    assert result["branches"]["link"]["status"] == "blocked"
    assert result["branches"]["live"]["status"] == "blocked"


def test_operation_key_stable_and_unknown_submission_not_retried(tmp_path):
    c = load_config(config(tmp_path))
    repo = Repository(c.state_db)
    orch = Orchestrator(c, repo)
    key1 = orch.operation_key("link", "ingest", {"url": "https://example.com/a"})
    key2 = orch.operation_key("link", "ingest", {"url": "https://example.com/a"})
    assert key1 == key2
    with pytest.raises(SubmissionUnknown):
        orch.submit_with_unknown("link", key1)
    assert repo.operation_status(key1) == "submission_unknown"


def test_resume_preserves_unknown_remote_task_and_cancel(tmp_path):
    c = load_config(config(tmp_path))
    repo = Repository(c.state_db)
    orch = Orchestrator(c, repo)
    run_id = repo.create_run(c.snapshot())
    repo.record_remote_task(run_id, "remote-123", "unknown")
    orch.cancel(run_id)
    assert repo.get_run(run_id)["status"] == "cancelled"
    assert repo.get_remote_tasks(run_id)[0]["status"] == "unknown"


def test_sanitize_and_dynamic_payload_cannot_change_fixed_config():
    secret = "Bearer abc123"
    assert secret not in sanitize({"message": secret})["message"]
    with pytest.raises(ValueError):
        validate_dynamic_payload({"publish": True, "topic": "x"}, fixed={"publish": False})


def test_config_loads_env_overrides_without_persisting_secrets(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MATRIXGEN_BASE_URL=https://env.example\nMATRIXGEN_TOKEN=secret-token\nMATRIXGEN_FOLDER_ID=folder-1\n")
    data = config(tmp_path, mode="remote_live")
    data["target"]["base_url"] = "https://json.example"
    c = load_config(data, env_file=env)
    assert c.data["target"]["base_url"] == "https://env.example"
    assert c.data["profiles"]["folder_id"] == "folder-1"
    assert "secret-token" not in json.dumps(c.snapshot())


def test_remote_adapter_builds_config_route_without_calling_unknown_route(tmp_path):
    from orchestrator.adapters import RemoteAdapter
    data = config(tmp_path, mode="remote_live")
    data["target"] = {"base_url": "https://example.test", "api_prefix": "/api/v1", "allowed_hosts": ["example.test"]}
    data["remote_api"] = {"routes": {"vfc_config": "GET /vfc/config"}}
    c = load_config(data)
    adapter = RemoteAdapter(c)
    assert adapter._route("vfc_config") == ("GET", "/vfc/config")
    with pytest.raises(RuntimeError, match="REMOTE_ROUTE_UNCONFIGURED"):
        adapter.request("missing", {})

    data = json.loads((ROOT / "configs" / "remote-test.local.json").read_text())
    data["state_db"] = str(tmp_path / "state.db")
    c = load_config(data)
    assert c.mode == "remote_live"


def test_search_link_cli_returns_json_blocked_for_missing_provider():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator", "search-link", "--config", str(ROOT / "configs" / "remote-test.local.json"), "--query", "test", "--limit", "1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"

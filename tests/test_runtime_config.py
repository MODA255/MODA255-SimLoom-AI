import json

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.queue import TaskQueue
from engineering_agent.runtime_config import RuntimeConfigError, RuntimeConfigStore, merge, normalize, snapshot
from engineering_agent.web import create_server


def test_runtime_config_normalizes_connection_fields_and_rejects_secrets():
    value = normalize({
        "tools": {"catia": {"bridge_url": " http://worker:8099 ",
                              "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN", "executable": "catia.exe"}},
        "windows_bridge": {"host": "127.0.0.1", "port": 8099, "root_dir": "C:/jobs", "timeout_seconds": 1800},
    })
    assert value["tools"]["catia"]["bridge_url"] == "http://worker:8099"
    with pytest.raises(RuntimeConfigError, match="unsupported tools.catia field"):
        normalize({"tools": {"catia": {"bridge_token": "secret"}}})
    with pytest.raises(RuntimeConfigError, match="port"):
        normalize({"windows_bridge": {"port": 70000}})
    with pytest.raises(RuntimeConfigError, match="environment variable"):
        normalize({"tools": {"catia": {"bridge_token_env": "not-valid"}}})


def test_runtime_config_store_writes_atomically_and_merges_without_mutating_base(tmp_path):
    path = tmp_path / "simloom-runtime-config.json"
    store = RuntimeConfigStore(path)
    saved = store.write({"tools": {"ansys": {"bridge_url": "https://worker.example"}}})
    assert saved["schema_version"] == "simloom-ai/runtime-config/v1"
    assert json.loads(path.read_text())["tools"]["ansys"]["bridge_url"] == "https://worker.example"
    base = {"tools": {"ansys": {"actions": {"mesh_and_solve": {"script": "run.py"}}, "bridge_url": "old"}},
            "windows_bridge": {"port": 8099}}
    merged = merge(base, store.read())
    assert merged["tools"]["ansys"]["bridge_url"] == "https://worker.example"
    assert merged["tools"]["ansys"]["actions"]["mesh_and_solve"]["script"] == "run.py"
    assert base["tools"]["ansys"]["bridge_url"] == "old"


def test_agent_runtime_override_rebuilds_adapter_without_exposing_token():
    agent = EngineeringAgent({"tools": {"catia": {"bridge_url": "http://old"}}})
    agent.apply_runtime_config({"tools": {"catia": {"bridge_url": "http://new"}}})
    assert agent.adapters[next(key for key in agent.adapters if key.value == "catia")].bridge_url == "http://new"
    assert "bridge_token" not in snapshot(agent.config)["tools"]["catia"]


def test_settings_http_round_trip_is_redacted_and_applies_to_agent(tmp_path):
    queue = TaskQueue(tmp_path / "workspace.db")
    agent = EngineeringAgent({"tools": {"catia": {"bridge_url": "http://old"}}})
    server = create_server(agent, queue, port=0)
    import threading
    import urllib.request

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/settings") as response:
            initial = json.loads(response.read())
        assert initial["settings"]["tools"]["catia"]["bridge_url"] == "http://old"
        assert initial["profile"]["schema_version"] == "simloom-ai/action-profile/v1"
        payload = json.dumps({"settings": {"tools": {"catia": {
            "bridge_url": "http://new:8099", "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN"}},
            "windows_bridge": {"port": 8100}}}).encode()
        request = urllib.request.Request(base + "/api/settings", data=payload,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            saved = json.loads(response.read())
        assert saved["saved"] is True
        assert saved["settings"]["tools"]["catia"]["bridge_url"] == "http://new:8099"
        assert agent.adapters[next(key for key in agent.adapters if key.value == "catia")].bridge_url == "http://new:8099"
        assert "bridge_token" not in saved["settings"]["tools"]["catia"]
        assert saved["profile"]["tools"]["catia"]["mode"] == "bridge"
        assert (tmp_path / "simloom-runtime-config.json").is_file()
        with urllib.request.urlopen(base + "/api/settings/profile") as response:
            exported = json.loads(response.read())
            assert exported["schema_version"] == "simloom-ai/action-profile/v1"
            assert "attachment" in response.headers.get("Content-Disposition", "")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)

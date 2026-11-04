import copy
from pathlib import Path

from orchestrator.phase1_runner import phase1_readiness
from orchestrator.phase1_suite import _read_config


CONFIG = Path(__file__).parents[1] / "local" / "phase1" / "run-config.json"


def _config():
    return _read_config(CONFIG)


def test_phase1_route_contract_is_read_only_and_structured():
    config = _config()
    config["remote_api"]["routes"].pop("import_url")
    result = phase1_readiness(config, strict=True)
    assert result["status"] == "blocked"
    assert result["writes"] == 0
    assert any("import_url" in error for error in result["errors"])


def test_missing_each_route_blocks_with_zero_writes():
    for section, name in (("remote_api", "vfc_config"),
                          ("remote_api", "list_assets"),
                          ("automation_routes", "get_task"), ("automation_routes", "get_ip_promo_phases")):
        candidate = copy.deepcopy(_config())
        if section == "remote_api":
            candidate[section]["routes"].pop(name)
        else:
            candidate[section].pop(name)
        result = phase1_readiness(candidate, strict=True)
        assert result["status"] == "blocked"
        assert result["writes"] == 0
        assert any(name in error for error in result["errors"])


def test_source_probe_route_is_optional_and_readiness_never_calls_remote_adapter():
    config = _config()
    config["remote_api"]["routes"].pop("source_probe")

    class NoRemoteCalls:
        def probe_capabilities(self):
            raise AssertionError("readiness must not call remote capability probes")

    result = phase1_readiness(config, adapter=NoRemoteCalls(), strict=True)
    assert result["status"] == "ready"
    assert result["writes"] == 0
    assert result["local_test"]["live_source_probe_runner"] is True


def test_readiness_enforces_get_read_routes_and_post_import_route():
    config = _config()
    config["remote_api"]["routes"]["list_assets"] = "POST /assets"
    config["remote_api"]["routes"]["import_url"] = "GET /assets/import-url"
    result = phase1_readiness(config, strict=True)
    assert result["status"] == "blocked"
    assert any("list_assets" in error and "GET" in error for error in result["errors"])
    assert any("import_url" in error and "POST" in error for error in result["errors"])

def test_readiness_validates_upload_routes_as_post_and_includes_get_upload_config():
    config = _config()
    config['enabled_chains'] = ['I-UPLOAD']
    config['remote_api']['routes']['upload_file'] = 'GET /assets/upload'
    result = phase1_readiness(config, strict=True)
    assert result['status'] == 'blocked'
    assert result['writes'] == 0
    assert any('upload_file' in error and 'POST' in error for error in result['errors'])

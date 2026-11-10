import copy

import pytest

from orchestrator.phase1_runner import phase1_readiness
from orchestrator.phase1_suite import _read_config


@pytest.fixture
def phase1_config(phase1_run_config):
    return _read_config(phase1_run_config)


def test_phase1_route_contract_is_read_only_and_structured(phase1_config):
    config = phase1_config
    config["remote_api"]["routes"].pop("import_url")
    result = phase1_readiness(config, strict=True)
    assert result["status"] == "blocked"
    assert result["writes"] == 0
    assert any("import_url" in error for error in result["errors"])


def test_missing_each_route_blocks_with_zero_writes(phase1_config):
    for section, name in (("remote_api", "vfc_config"),
                          ("remote_api", "list_assets"),
                          ("automation_routes", "get_task"), ("automation_routes", "get_ip_promo_phases")):
        candidate = copy.deepcopy(phase1_config)
        if section == "remote_api":
            candidate[section]["routes"].pop(name)
        else:
            candidate[section].pop(name)
        result = phase1_readiness(candidate, strict=True)
        assert result["status"] == "blocked"
        assert result["writes"] == 0
        assert any(name in error for error in result["errors"])


def test_source_probe_route_is_optional_and_readiness_never_calls_remote_adapter(phase1_config):
    config = phase1_config
    config["remote_api"]["routes"].pop("source_probe")

    class NoRemoteCalls:
        def probe_capabilities(self):
            raise AssertionError("readiness must not call remote capability probes")

    result = phase1_readiness(config, adapter=NoRemoteCalls(), strict=True)
    assert result["status"] == "ready"
    assert result["writes"] == 0
    assert result["local_test"]["live_source_probe_runner"] is True


def test_readiness_enforces_get_read_routes_and_post_import_route(phase1_config):
    config = phase1_config
    config["remote_api"]["routes"]["list_assets"] = "POST /assets"
    config["remote_api"]["routes"]["import_url"] = "GET /assets/import-url"
    result = phase1_readiness(config, strict=True)
    assert result["status"] == "blocked"
    assert any("list_assets" in error and "GET" in error for error in result["errors"])
    assert any("import_url" in error and "POST" in error for error in result["errors"])


def test_readiness_validates_upload_routes_as_post_and_includes_get_upload_config(phase1_config):
    config = phase1_config
    config["enabled_chains"] = ["I-UPLOAD"]
    config["remote_api"]["routes"]["upload_file"] = "GET /assets/upload"
    result = phase1_readiness(config, strict=True)
    assert result["status"] == "blocked"
    assert result["writes"] == 0
    assert any("upload_file" in error and "POST" in error for error in result["errors"])

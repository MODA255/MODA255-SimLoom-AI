import json
from pathlib import Path

from orchestrator.cli import _merge_phase1_remote_references
from orchestrator.phase1_runner import phase1_readiness


def test_remote_config_candidate_is_found_from_local_phase1_path(tmp_path):
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    (repo / "local" / "phase1").mkdir(parents=True)
    source = repo / "configs" / "remote-test.local.json"
    source.write_text(json.dumps({"remote_api": {"routes": {"vfc_config": "GET /health"}}}))
    config = repo / "local" / "phase1" / "run-config.json"
    config.write_text("{}")
    merged = _merge_phase1_remote_references({}, config)
    assert merged["remote_api"]["routes"]["vfc_config"] == "GET /health"


def test_local_override_wins_over_candidate_config(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "remote-test.local.json").write_text(json.dumps({"remote_api": {"routes": {"vfc_config": "GET /health"}}}))
    config = tmp_path / "run.json"
    merged = _merge_phase1_remote_references({"remote_api": {"routes": {"vfc_config": "GET /local"}}}, config)
    assert merged["remote_api"]["routes"]["vfc_config"] == "GET /local"


def test_strict_remote_readiness_requires_valid_routes_without_network(tmp_path):
    suite = json.loads((Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json").read_text())
    suite["folder"] = "folder-real"
    for key in suite["inputs"]:
        suite["inputs"][key] = "https://real.test/video" if "FILE" not in key else str(tmp_path / "video.mp4")
    (tmp_path / "video.mp4").write_bytes(b"video")
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(json.dumps(suite))
    result = phase1_readiness({"mode": "remote_live", "phase1": {"suite": str(suite_file)}}, strict=True)
    assert result["status"] == "blocked"
    assert any("routes" in error for error in result["errors"])

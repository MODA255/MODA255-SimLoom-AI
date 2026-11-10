from pathlib import Path

from orchestrator.phase1_runner import phase1_readiness
from orchestrator.phase1_suite import _read_config, load_phase1_suite, validate_phase1_resources

CONFIG = Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json"


def test_local_run_config_resolves_relative_suite_and_local_env(phase1_run_config, tmp_path, monkeypatch):
    # A different working directory proves the suite and upload paths do not
    # accidentally depend on the checkout's ignored local files.
    monkeypatch.chdir(tmp_path)
    config = _read_config(phase1_run_config)
    assert config["phase1"]["suite"] == "../../configs/daily-nine-chain-phase1.json"
    assert config["_env_values"]["MATRIXGEN_FOLDER_ID"] == "fixture-folder"
    assert config["_env_values"]["DAILY_DOUYIN_URL"] == "https://fixture.test/douyin/video"
    assert Path(config["_env_values"]["DAILY_UPLOAD_FILE"]).is_file()

    result = phase1_readiness(config)

    assert result["status"] == "ready"
    assert result["suite_id"] == "daily-nine-chain-phase1-v1"
    assert result["chains"] == [
        "I-LINK", "I-LIVE", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME",
        "A-ECOM", "A-IP", "A-THEME",
    ]


def _env(monkeypatch, tmp_path, url="https://real.test/video"):
    for key in ("DAILY_DOUYIN_URL", "DAILY_WEIBO_URL", "DAILY_DOUYIN_URL_ALT", "DAILY_WEIBO_URL_ALT", "DAILY_LIVE_URL"):
        monkeypatch.setenv(key, url)
    monkeypatch.setenv("MATRIXGEN_FOLDER_ID", "folder-real")
    upload = tmp_path / "upload.mp4"
    upload.write_bytes(b"video")
    monkeypatch.setenv("DAILY_UPLOAD_FILE", str(upload))
    return upload


def test_production_placeholders_are_blocked(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "https://example.invalid/video")
    suite = load_phase1_suite(CONFIG)
    errors = validate_phase1_resources(suite)
    assert any("placeholder" in error for error in errors)
    assert phase1_readiness({"phase1": {"suite": str(CONFIG)}})["status"] == "blocked"


def test_environment_references_resolve_runner_inputs(monkeypatch, tmp_path):
    upload = _env(monkeypatch, tmp_path)
    suite = load_phase1_suite(CONFIG)
    assert validate_phase1_resources(suite) == []
    link = next(c for chain in suite["chains"] for c in chain["cases"] if c["case_id"] == "I-LINK-DOUYIN-001")
    upload_case = next(c for chain in suite["chains"] for c in chain["cases"] if c["case_id"] == "I-UPLOAD-001")
    assert link["payload"]["url"] == "https://real.test/video"
    assert link["folder_id"] == "folder-real"
    assert upload_case["payload"]["file_path"] == str(upload)

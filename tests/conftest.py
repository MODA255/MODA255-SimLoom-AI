"""Explicit test-only fixtures; production defaults remain restrictive."""
import json
import os
from pathlib import Path

import pytest
from orchestrator.policy import SourcePolicy


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def isolated_orchestrator_env(monkeypatch):
    """Keep these offline configuration tests independent of developer credentials."""
    for name in tuple(os.environ):
        if name.startswith(("MATRIXGEN_", "DAILY_")) or name == "TEST_SOURCE_URL":
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def remote_test_config(tmp_path, isolated_orchestrator_env):
    """A fresh, sanitized adapter config with only temporary state paths."""
    config = json.loads((FIXTURES / "phase1-run-config.json").read_text(encoding="utf-8"))
    config["state_db"] = str(tmp_path / "state.db")
    return config


@pytest.fixture
def remote_config_path(tmp_path, remote_test_config):
    """Give subprocess CLI tests a config without consulting a checkout's .env."""
    config_dir = tmp_path / "remote"
    config_dir.mkdir()
    path = config_dir / "config.json"
    path.write_text(json.dumps(remote_test_config), encoding="utf-8")
    (config_dir / ".env").write_text("# Test-only empty environment.\n", encoding="utf-8")
    return path


@pytest.fixture
def phase1_run_config(tmp_path, remote_test_config):
    """Recreate the relative suite/.env layout using only tracked test data."""
    config_dir = tmp_path / "local" / "phase1"
    config_dir.mkdir(parents=True)
    suite_dir = tmp_path / "configs"
    suite_dir.mkdir()
    suite_name = "daily-nine-chain-phase1.json"
    (suite_dir / suite_name).write_text(
        (ROOT / "configs" / suite_name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    upload = config_dir / "sample-upload.mp4"
    # Readiness only checks file existence; this is not an encoded video.
    upload.write_bytes(b"fixture-upload-placeholder\n")
    # Generate a temporary .env instead of committing an exception to the
    # repository's credential-file ignore rules.
    env_values = {
        "MATRIXGEN_FOLDER_ID": "fixture-folder",
        "DAILY_DOUYIN_URL": "https://fixture.test/douyin/video",
        "DAILY_WEIBO_URL": "https://fixture.test/weibo/video",
        "DAILY_DOUYIN_URL_ALT": "https://fixture.test/douyin/video-alt",
        "DAILY_WEIBO_URL_ALT": "https://fixture.test/weibo/video-alt",
        "DAILY_LIVE_URL": "https://fixture.test/live/room",
        "DAILY_UPLOAD_FILE": str(upload),
    }
    (config_dir / ".env").write_text(
        "".join(f"{name}={value}\n" for name, value in env_values.items()), encoding="utf-8"
    )
    path = config_dir / "run-config.json"
    path.write_text(json.dumps(remote_test_config), encoding="utf-8")
    return path


@pytest.fixture
def explicit_source_fixture():
    policy = SourcePolicy.from_dict({
        "live": {"allowed_platforms": ["douyin", "weibo"], "rules": {
            "douyin": [{"host": "example.com", "path": "/", "types": ["live_url"]}],
        }},
        "content": {"allowed_url_types": ["direct_video", "verified_ingest_platform"], "rules": {
            "youtube": [{"host": "example.com", "path": "/", "types": ["verified_ingest_platform"]}, {"host":"fixed.test", "path":"/source", "types":["direct_video"]}, {"host":"x.test", "path":"/v", "types":["verified_ingest_platform"]}],
        }},
    })
    return policy

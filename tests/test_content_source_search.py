import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.content_source_search import (  # noqa: E402
    ContentSourceSearchInput,
    ContentSourceSearchSkill,
)


class SuccessfulProbe:
    def probe(self, source_url):
        from orchestrator.source_probe import SourceProbeResult
        return SourceProbeResult(success=True, platform="youtube")


class Provider:
    def __init__(self, value):
        self.value = value

    def search(self, query, limit=5):
        return self.value


def test_no_provider_is_blocked_and_does_not_fabricate_url():
    out = ContentSourceSearchSkill().run(ContentSourceSearchInput(query="cats"))
    assert out.status == "blocked"
    assert out.candidates == []
    assert "url" not in json.dumps(out.model_dump()).lower() or "https://" not in json.dumps(out.model_dump())


def test_normalizes_list_and_filters_platform_required_and_deduplicates_canonical_url(explicit_source_fixture):
    # Content-only fixture: example.com is also a live host in the shared fixture.
    explicit_source_fixture.data["live"] = {}
    rows = [
        {"url": "https://Example.com/a/?utm_source=x", "title": "A", "platform": "youtube", "content_type": "video", "authorization_status": "authorized"},
        {"source_url": "https://example.com/a", "title": "duplicate", "platform": "youtube"},
        {"url": "https://other.com/b", "platform": "tiktok", "title": "B"},
    ]
    request = ContentSourceSearchInput(query="q", allowed_platforms=["youtube"], required=["video"])
    out = ContentSourceSearchSkill(Provider(rows), source_policy=explicit_source_fixture, probe_gateway=SuccessfulProbe()).run(request)
    assert out.status == "success"
    assert len(out.candidates) == 1
    c = out.candidates[0]
    assert c.source_url == "https://example.com/a"
    assert c.candidate_id
    assert c.platform == "youtube"
    assert c.content_type == "video"
    for field in ("duration", "orientation", "audio_features", "visual_features", "business_tags", "freshness", "duplicate_status", "authorization_status", "evidence", "selection_reason", "next_action"):
        assert hasattr(c, field)


def test_dict_results_and_must_be_new_asset_and_unknown_facts_are_not_filled(explicit_source_fixture):
    row = {"url": "https://x.test/v", "platform": "youtube", "duplicate_status": "duplicate", "authorization_status": "unknown"}
    out = ContentSourceSearchSkill(Provider({"results": [row]}), source_policy=explicit_source_fixture, probe_gateway=SuccessfulProbe()).run(
        ContentSourceSearchInput(query="q", must_be_new_asset=True, require_authorization=True)
    )
    assert out.status == "no_candidate"
    assert out.candidates == []
    assert out.validation[0]["reason"] in {"duplicate", "authorization_unknown"}


def test_unknown_authorization_requires_review(explicit_source_fixture):
    out = ContentSourceSearchSkill(Provider([{"url": "https://x.test/v", "authorization_status": "unknown"}]), source_policy=explicit_source_fixture, probe_gateway=SuccessfulProbe()).run(
        ContentSourceSearchInput(query="q", require_authorization=True)
    )
    assert out.status == "needs_review"
    assert out.candidates[0].authorization_status == "unknown"


def test_fixed_fallback_is_explicit_only(monkeypatch, explicit_source_fixture):
    monkeypatch.setenv("TEST_SOURCE_URL", "https://fixed.test/source")
    blocked = ContentSourceSearchSkill().run(ContentSourceSearchInput(query="q"))
    assert blocked.status == "blocked"
    out = ContentSourceSearchSkill(source_policy=explicit_source_fixture, probe_gateway=SuccessfulProbe()).run(ContentSourceSearchInput(query="q", allow_fixed_fallback=True))
    assert out.status == "fixed_source_fallback"
    assert out.candidates[0].source_url == "https://fixed.test/source"


def test_secret_is_not_in_cli_output(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"providers": {}}))
    env = os.environ.copy()
    env["WEB_SEARCH_TOKEN"] = "DO_NOT_PRINT_ME"
    proc = subprocess.run([sys.executable, "-m", "orchestrator", "search-link", "--config", str(config), "--query", "q"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert proc.returncode == 0
    assert "blocked" in proc.stdout
    assert "DO_NOT_PRINT_ME" not in proc.stdout


def test_cli_search_link_supports_limit(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"providers": {}, "allow_fixed_fallback": False}))
    proc = subprocess.run([sys.executable, "-m", "orchestrator", "search-link", "--config", str(config), "--query", "q", "--limit", "2"], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["status"] == "blocked"


def test_source_policy_blocks_empty_allowlist_and_unknown_host(tmp_path):
    from orchestrator.policy import SourcePolicy
    policy = SourcePolicy.from_dict({"content": {"allowed_url_types": [], "rules": {}}})
    assert not policy.validate_content("https://example.com/a.mp4", {"content_type": "direct_video"}).allowed


def test_source_policy_ignores_forged_labels_and_rejects_userinfo_suffix_spoof():
    from orchestrator.policy import SourcePolicy
    policy = SourcePolicy.from_dict({"content": {"rules": {"youtube": [{"host": "youtube.com", "path": "/watch", "types": ["verified_ingest_platform"]}]}}})
    assert not policy.validate_content("https://evil@youtube.com.evil.test/watch", {"platform": "youtube", "verified": True, "content_type": "verified_ingest_platform"}).allowed
    assert not policy.validate_content("https://youtube.com.evil.test/watch", {"platform": "youtube", "content_type": "verified_ingest_platform"}).allowed


def test_source_policy_allows_explicit_host_path_type_rule_and_mp4_direct_is_not_enough():
    from orchestrator.policy import SourcePolicy
    policy = SourcePolicy.from_dict({"content": {"rules": {"trusted": [{"host": "media.example", "path": "/clips/", "types": ["verified_ingest_platform"]}]}}})
    assert policy.validate_content("https://media.example/clips/a", {"content_type": "verified_ingest_platform"}).allowed
    assert not policy.validate_content("https://media.example/a.mp4", {"content_type": "direct_video"}).allowed


def test_tencent_video_is_unverified_not_unsupported():
    from orchestrator.policy import SourcePolicy
    policy = SourcePolicy.from_dict({"content": {"rules": {}}})
    result = policy.validate_content("https://v.qq.com/x", {"platform": "tencent_video", "content_type": "unknown"})
    assert result.reason == "unverified" and result.status == "unverified"

@pytest.mark.parametrize('url', ['https://unknown.test/a.mp4', 'https://user@media.example/clips/a', 'https://media.example.evil/clips/a', 'not a URL', 'https://[broken'])
def test_search_rejects_forged_provider_and_invalid_urls(url, explicit_source_fixture):
    row = {'url': url, 'platform': 'youtube', 'verified': True, 'url_type': 'direct_video'}
    out = ContentSourceSearchSkill(Provider([row]), source_policy=explicit_source_fixture).run(ContentSourceSearchInput(query='q'))
    assert not out.candidates
    assert out.validation


def test_default_policy_and_fallback_are_fail_closed(monkeypatch):
    monkeypatch.setenv('TEST_SOURCE_URL', 'https://unknown.test/a.mp4')
    out = ContentSourceSearchSkill().run(ContentSourceSearchInput(query='q', allow_fixed_fallback=True))
    assert out.status == 'blocked' and not out.candidates


def test_cli_cannot_bypass_policy_via_fixed_fallback(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'providers': {}, 'allow_fixed_fallback': True}))
    env = dict(os.environ, TEST_SOURCE_URL='https://unknown.test/a.mp4')
    proc = subprocess.run([sys.executable, '-m', 'orchestrator', 'search-link', '--config', str(config), '--query', 'q'], cwd=ROOT, env=env, text=True, capture_output=True)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)['status'] == 'blocked'


def test_rule_type_mismatch_and_empty_platform_allowlist():
    from orchestrator.policy import SourcePolicy
    data = {'content': {'allowed_platforms': [], 'rules': {'trusted': [{'host': 'media.example', 'path': '/clips/', 'types': ['direct_video']}]}}}
    assert not SourcePolicy.from_dict(data).validate_content('https://media.example/clips/a').allowed
    data['content']['allowed_platforms'] = ['trusted']
    policy = SourcePolicy.from_dict(data)
    assert policy.validate_content('https://media.example/clips/a').allowed
    assert not policy.validate_content('https://media.example/clips/a', {'url_type': 'live_url'}).allowed

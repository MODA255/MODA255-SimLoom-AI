import pytest

from orchestrator.content_source_search import ContentSourceSearchInput, ContentSourceSearchSkill
from orchestrator.policy import SourcePolicy


class Provider:
    def __init__(self, url, **metadata):
        self.row = dict(url=url, **metadata)

    def search(self, query, limit=5):
        return [self.row]


class Probe:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    def probe(self, source_url):
        from orchestrator.source_probe import SourceProbeResult
        self.calls.append(source_url)
        if isinstance(self.success, Exception):
            raise self.success
        return SourceProbeResult(success=self.success, platform="tencent_video")


def test_gateway_contract():
    from orchestrator.source_probe import SourceProbeGateway, SourceProbeResult
    assert isinstance(Probe(), SourceProbeGateway)
    assert SourceProbeResult(success=True).platform is None


def test_probe_success_is_ingest_verification_not_authorization():
    probe = Probe()
    out = ContentSourceSearchSkill(Provider("https://v.qq.com/x/cover/a", verified=True), probe_gateway=probe).run(
        ContentSourceSearchInput(query="q", require_authorization=True))
    assert out.status == "needs_review"
    candidate = out.candidates[0]
    assert candidate.url_type == "verified_ingest_platform"
    assert candidate.authorization_status == "unknown"
    assert candidate.platform == "tencent_video"
    assert probe.calls == [candidate.source_url]
    assert candidate.evidence == [{"kind": "source_probe", "success": True}]


@pytest.mark.parametrize("result", [False, RuntimeError("secret")])
def test_failed_probe_blocks_even_allowlisted_source(result):
    policy = SourcePolicy.from_dict({"content": {"rules": {"trusted": [
        {"host": "media.example", "path": "/", "types": ["direct_video"]}]}}})
    out = ContentSourceSearchSkill(Provider("https://media.example/a.mp4"), source_policy=policy, probe_gateway=Probe(result)).run(ContentSourceSearchInput(query="q"))
    assert out.status == "blocked" and not out.candidates
    assert "secret" not in out.model_dump_json()


def test_missing_probe_blocks_even_allowlisted_source():
    policy = SourcePolicy.from_dict({"content": {"rules": {"trusted": [
        {"host": "media.example", "path": "/", "types": ["direct_video"]}]}}})
    out = ContentSourceSearchSkill(Provider("https://media.example/a.mp4"), source_policy=policy).run(ContentSourceSearchInput(query="q"))
    assert out.status == "blocked"


@pytest.mark.parametrize("url,metadata", [
    ("https://live.douyin.com/123", {}),
    ("https://live.weibo.com/show?id=123", {}),
    ("https://example.org/room", {"url_type": "live_url"}),
    ("https://example.org/room", {"content_type": "live"}),
    ("https://user@example.org/a", {}),
    ("https://127.0.0.1/a", {}),
    ("not a URL", {}),
])
def test_unsafe_or_live_sources_never_reach_probe(url, metadata):
    probe = Probe()
    out = ContentSourceSearchSkill(Provider(url, **metadata), probe_gateway=probe).run(ContentSourceSearchInput(query="q"))
    assert out.status == "blocked" and not out.candidates
    assert probe.calls == []


def test_fixed_fallback_also_requires_probe(monkeypatch):
    monkeypatch.setenv("TEST_SOURCE_URL", "https://v.qq.com/x/cover/a")
    probe = Probe(False)
    out = ContentSourceSearchSkill(probe_gateway=probe).run(ContentSourceSearchInput(query="q", allow_fixed_fallback=True))
    assert out.status == "blocked" and probe.calls


def test_provider_cannot_forge_probe_result():
    out = ContentSourceSearchSkill(Provider("https://v.qq.com/x/cover/a", probe_success=True, url_type="verified_ingest_platform")).run(ContentSourceSearchInput(query="q"))
    assert out.status == "blocked"

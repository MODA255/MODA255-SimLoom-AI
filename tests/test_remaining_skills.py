import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.live_source_search import LiveSourceSearchInput, LiveSourceSearchSkill
from orchestrator.topic_generation import TopicGenerationInput, TopicGenerationSkill
from orchestrator.scenario_assembly import ScenarioAssemblyInput, ScenarioAssemblySkill


class LiveProvider:
    def __init__(self, value): self.value = value
    def search(self, query, limit=5): return self.value


def test_live_search_provider_required_and_canonicalizes_results(explicit_source_fixture):
    assert LiveSourceSearchSkill().run(LiveSourceSearchInput(query="x")).status == "blocked"
    out = LiveSourceSearchSkill(LiveProvider({"results": [{"url": "https://EXAMPLE.com/live/?utm_source=x", "platform": "douyin", "authorization_status": "unknown"}]}), source_policy=explicit_source_fixture).run(
        LiveSourceSearchInput(query="x", allowed_platforms=["douyin"], required=["live"])
    )
    assert out.status == "needs_review"
    assert out.results[0].url == "https://example.com/live"


def test_topic_generation_never_fabricates_without_template_or_model():
    blocked = TopicGenerationSkill().run(TopicGenerationInput(material_facts=[]))
    assert blocked.status in {"blocked", "validation_failed"}
    class Model:
        def generate(self, prompt): return {"topic": "真实主题", "brief": "brief", "content_angle": "angle"}
    out = TopicGenerationSkill(template_provider=lambda name: {"name": name, "version": "1"}, model_provider=Model()).run(
        TopicGenerationInput(material_facts=[{"asset_id": "a1", "title": "事实"}], template="t")
    )
    assert out.status == "success" and out.topic == "真实主题" and out.template == "t"


def test_topic_brief_empty_is_not_injected():
    class Model:
        def generate(self, prompt): return {"topic": "主题", "brief": ""}
    out = TopicGenerationSkill(template_provider=lambda name: {"name": name, "version": "1"}, model_provider=Model()).run(
        TopicGenerationInput(material_facts=[{"asset_id": "a1"}], template="t")
    )
    assert out.brief is None


def test_scenario_assembly_validates_ready_assets_and_is_idempotent(explicit_source_fixture):
    request = ScenarioAssemblyInput(assets=[{"asset_id": "a1", "status": "ready", "source_type": "link", "source_url": "https://example.com/a", "content_type": "verified_ingest_platform", "entity_facts": ["brand"]}], topic={"topic": "T", "entity_facts": ["brand"]}, fixed_config={"publish": False})
    first = ScenarioAssemblySkill(source_policy=explicit_source_fixture).run(request)
    second = ScenarioAssemblySkill(source_policy=explicit_source_fixture).run(request)
    assert first.status == "success" and first.scenario_id == second.scenario_id
    assert first.task is None
    bad = ScenarioAssemblySkill(source_policy=explicit_source_fixture).run(request.model_copy(update={"assets": [{"asset_id": "a1", "status": "processing"}]}))
    assert bad.status in {"blocked", "validation_failed", "needs_review"}


def test_new_cli_commands_return_json_without_traceback(tmp_path):
    config = tmp_path / "config.json"; config.write_text(json.dumps({"providers": {}}))
    root = Path(__file__).parents[1]
    for cmd in ("search-live", "generate-topic", "assemble-scenario"):
        args = [sys.executable, "-m", "orchestrator", cmd, "--config", str(config)]
        if cmd == "search-live": args += ["--query", "x"]
        proc = subprocess.run(args, cwd=root, text=True, capture_output=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["status"]
        assert "Traceback" not in proc.stderr


def test_live_policy_only_allows_douyin_weibo_and_does_not_claim_recording():
    from orchestrator.live_source_search import LiveSourceSearchSkill
    class P:
        def search(self, query, limit=5):
            return [{"url":"https://live.example/x", "platform":"douyin", "verified":True}]
    out = LiveSourceSearchSkill(P()).run(LiveSourceSearchInput(query="x"))
    assert out.status == "no_candidate"


def test_ready_asset_and_model_claims_do_not_bypass_policy():
    request = ScenarioAssemblyInput(assets=[{'asset_id':'a', 'status':'ready', 'source_type':'link', 'source_url':'https://unknown.test/a.mp4', 'verified':True, 'platform':'douyin'}], topic={'topic':'T'})
    out = ScenarioAssemblySkill().run(request)
    assert out.status == 'blocked' and out.scenario_id is None


def test_live_default_allowed_hosts_are_candidates_only():
    out = LiveSourceSearchSkill(LiveProvider([{'url':'https://live.douyin.com/123', 'platform':'youtube'}])).run(LiveSourceSearchInput(query='q'))
    assert out.results[0].platform == 'douyin'
    assert out.validation[-1]['recording_verified'] is False

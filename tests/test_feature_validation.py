import json
from pathlib import Path
from orchestrator.feature_validation import validate_features
from orchestrator.scene_profiles import load_feature_profiles
from orchestrator.scenario_assembly import ScenarioAssemblyInput, ScenarioAssemblySkill

ROOT = Path(__file__).parents[1]


def asset(**kw):
    return {"asset_id": kw.pop("asset_id", "a1"), "status": "ready", "source_type": "content", "source_url": "https://x.test/v", **kw}


def test_profiles_load_all():
    profiles = load_feature_profiles(ROOT / "configs/feature-config-matrix.json")
    assert {f"F{i}" for i in range(7)} <= set(profiles)


def test_f0_is_allowed():
    out = validate_features({}, [asset()], {"topic": "x"})
    assert out["status"] == "allowed"
    assert out["profile_id"] == "F0"


def test_bgm_conflict():
    out = validate_features({"bgm_mode": "library"}, [asset()], {"topic": "x"})
    assert out["status"] == "blocked" and out["blocked_rules"]


def test_selected_empty_and_portrait_empty():
    assert validate_features({"subtitle_style_mode": "selected", "subtitle_selected": []}, [asset()], {})["status"] == "blocked"
    assert validate_features({"portrait_style": "selected", "portrait_styles": []}, [asset()], {})["status"] == "blocked"


def test_person_opening_and_dynamic_crop_requirements():
    assert validate_features({"opening_mode": "person"}, [asset()], {})["status"] == "blocked"
    out = validate_features({"force_portrait": True, "enable_dynamic_crop": True}, [asset(aspect_ratio=1.0)], {})
    assert out["status"] == "blocked"


def test_ip_gate_and_delivery_and_dynamic_override():
    cfg = {"recipe": "ip_promo", "num_versions": 1, "delivery_enabled": False}
    out = validate_features(cfg, [asset(ip_name="IP")], {}, profile_id="F0", dynamic_config={"num_versions": 3, "delivery_enabled": True})
    assert out["status"] == "blocked"
    assert any("fixed" in x or "delivery" in x for x in out["blocked_rules"])


def test_assembly_exposes_feature_result():
    out = ScenarioAssemblySkill().run(ScenarioAssemblyInput(assets=[asset()], topic={"topic": "x"}, fixed_config={}))
    assert "profile_id" in out.model_dump()
    assert out.validation

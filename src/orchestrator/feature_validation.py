from __future__ import annotations
from .scene_profiles import load_feature_profiles


def validate_features(config, assets, topic, profile_id="F0", dynamic_config=None, profiles=None):
    profiles = profiles or load_feature_profiles()
    if profile_id not in profiles: return {"status":"blocked","profile_id":profile_id,"passed_rules":[],"warnings":[],"blocked_rules":["profile_unknown"]}
    fixed = dict(profiles[profile_id]); fixed.pop("profile_id", None); fixed.pop("name", None)
    supplied = dict(config or {})
    dynamic = dict(dynamic_config or {})
    passed, warnings, blocked = [], [], []
    for key, value in dynamic.items():
        if key in fixed and value != fixed[key]: blocked.append(f"fixed_config_override:{key}")
    merged = dict(fixed); merged.update(supplied)
    delivery_keys = profiles.get("_rules", {}).get("delivery", {}).get("keys", [])
    for key in delivery_keys:
        if merged.get(key) not in (None, False, [], ""): blocked.append(f"delivery_disabled:{key}")
    bgm = merged.get("bgm_mode", "none")
    if bgm not in ("none", "library", "auto"): blocked.append("bgm_mode_invalid")
    elif bgm == "library" and not (merged.get("bgm_asset") or merged.get("bgm_library") or supplied.get("bgm_asset_or_library")): blocked.append("bgm_library_missing")
    if merged.get("subtitle_style_mode") == "selected" and not merged.get("subtitle_selected"): blocked.append("subtitle_selected_empty")
    if merged.get("portrait_style") == "selected" and not merged.get("portrait_styles"): blocked.append("portrait_selected_empty")
    if merged.get("opening_mode") == "person" and not any(a.get("person_detected") or a.get("has_person") for a in assets): blocked.append("person_opening_without_person")
    if merged.get("force_portrait") and merged.get("enable_dynamic_crop") and not any(float(a.get("aspect_ratio", 0)) > 1.2 for a in assets): blocked.append("dynamic_crop_requires_landscape")
    if merged.get("recipe") == "ip_promo" or any(a.get("ip_name") for a in assets):
        rules = profiles.get("_rules", {}).get("ip", {})
        if len(assets) < rules.get("min_asset_count", 2): blocked.append("ip_min_asset_count")
        if sum(int(a.get("clip_count", 1)) for a in assets) < rules.get("min_clip_count", 2): blocked.append("ip_min_clip_count")
        if rules.get("require_asr") and not all(a.get("asr") or a.get("asr_text") for a in assets): blocked.append("ip_asr_required")
        if rules.get("require_ip_evidence") and not all(a.get("ip_evidence") or a.get("person_detected") or a.get("ip_name") for a in assets): blocked.append("ip_evidence_required")
    if not blocked: passed.extend(["profile_loaded", "delivery_closed", "fixed_config_intact"])
    status = "blocked" if blocked else ("needs_review" if warnings else "allowed")
    return {"status":status,"profile_id":profile_id,"passed_rules":passed,"warnings":warnings,"blocked_rules":blocked}

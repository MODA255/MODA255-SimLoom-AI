from __future__ import annotations
import json
from pathlib import Path

_DEFAULT = Path(__file__).parents[2] / "configs" / "feature-config-matrix.json"

def load_feature_profiles(path: str | Path = _DEFAULT) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = {k: dict(v.get("features", {}), profile_id=k, name=v.get("name", k)) for k,v in data["profiles"].items() if k.startswith("F")}
    profiles["_rules"] = data["profiles"].get("rules", {})
    return profiles

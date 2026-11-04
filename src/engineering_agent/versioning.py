from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingest import ArtifactReader
from .object_model import EngineeringObject, EngineeringProject


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionStore:
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.root = self.project_dir / ".engineering-agent" / "versions"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, label: str, objects: list[EngineeringObject] | EngineeringProject, parent: str | None = None) -> dict[str, Any]:
        assets = objects.assets if isinstance(objects, EngineeringProject) else objects
        version_id = str(uuid.uuid4())
        payload = {"id": version_id, "label": label, "parent": parent, "created_at": _now(), "objects": [asset.to_dict() for asset in assets]}
        (self.root / f"{version_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with (self.root / "timeline.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "version_created", "version_id": version_id, "label": label, "parent": parent, "created_at": payload["created_at"]}, ensure_ascii=False) + "\n")
        return payload

    def ingest_and_create(self, label: str, paths: list[str | Path], parent: str | None = None) -> dict[str, Any]:
        reader = ArtifactReader()
        return self.create(label, [reader.read(path) for path in paths], parent)

    def list(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            values.append(json.loads(path.read_text(encoding="utf-8")))
        return values

    def load(self, version_id: str) -> dict[str, Any]:
        path = self.root / f"{version_id}.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def diff(self, left: str, right: str) -> dict[str, Any]:
        before = self.load(left)
        after = self.load(right)
        changes = self._diff_value(before, after)
        return {"left": left, "right": right, "change_count": len(changes), "changes": changes}

    def timeline(self) -> list[dict[str, Any]]:
        path = self.root / "timeline.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _diff_value(self, before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
        if isinstance(before, dict) and isinstance(after, dict):
            changes = []
            for key in sorted(set(before) | set(after)):
                if key in {"object_id", "id", "created_at", "parent"}:
                    continue
                child = f"{path}.{key}" if path else key
                if key not in before:
                    changes.append({"path": child, "kind": "added", "after": after[key]})
                elif key not in after:
                    changes.append({"path": child, "kind": "removed", "before": before[key]})
                else:
                    changes.extend(self._diff_value(before[key], after[key], child))
            return changes
        if isinstance(before, list) and isinstance(after, list):
            if before == after:
                return []
            if len(before) == len(after) and all(isinstance(item, dict) for item in before + after):
                changes = []
                for index, (left_item, right_item) in enumerate(zip(before, after)):
                    changes.extend(self._diff_value(left_item, right_item, f"{path}[{index}]"))
                return changes
            return [{"path": path, "kind": "changed", "before": before, "after": after}]
        return [] if before == after else [{"path": path, "kind": "changed", "before": before, "after": after}]

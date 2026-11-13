from __future__ import annotations

import json
import hashlib
import re
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from .ingest import ArtifactReader
from .object_model import ArtifactRef, AssetKind, EngineeringObject, EngineeringProject
from .transactions import RunTransaction, confined_path, file_hash


VERSION_ASSET_EXTENSIONS = {".dxf", ".step", ".stp", ".obj", ".csv", ".json", ".svg", ".pdf", ".png",
                            ".catpart", ".catproduct", ".catdrawing", ".wbpj", ".mechdb", ".agdb",
                            ".rst", ".rth", ".res", ".adm", ".acf", ".cmd", ".sim", ".mat", ".xml"}


def _companions(path: str) -> list[str]:
    suffix = Path(path).suffix.lower()
    endings = [".cad.json"] if suffix == ".dxf" else [".brep.json"] if suffix in {".step", ".stp"} else [".plot.json"] if suffix in {".pdf", ".png", ".svg"} else []
    return [path + ending for ending in endings]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionStore:
    def __init__(self, project_dir: str | Path):
        self._input_root = Path(project_dir).expanduser().absolute()
        self.project_dir = self._input_root.resolve()
        self.root = confined_path(self.project_dir, ".engineering-agent/versions")
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("VERSION_INVALID_ID")
        return value

    def _relative(self, value: str | Path) -> str:
        path = Path(value).expanduser()
        if path.is_absolute():
            if path.is_relative_to(self._input_root):
                path = path.relative_to(self._input_root)
            elif path.is_relative_to(self.project_dir):
                path = path.relative_to(self.project_dir)
            else:
                raise ValueError("VERSION_PATH_OUTSIDE_PROJECT")
        confined_path(self.project_dir, str(path))
        if path.parts[0] == ".engineering-agent" or path.parts[:2] == ("artifacts", "runs"):
            raise ValueError("VERSION_INTERNAL_PATH")
        return path.as_posix()

    def _save(self, transaction, payload, generated=None, validate=None):
        timeline_event = {"event": "version_created", "version_id": payload["id"], "label": payload["label"],
                          "parent": payload["parent"], "created_at": payload["created_at"]}
        timeline_path = confined_path(self.root, "timeline.jsonl")
        prior = timeline_path.read_text(encoding="utf-8") if timeline_path.exists() else ""
        version_stage = transaction.stage_dir / "version.json"
        timeline_stage = transaction.stage_dir / "timeline.jsonl"
        version_stage.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        timeline_stage.write_text(prior + json.dumps(timeline_event, ensure_ascii=False) + "\n", encoding="utf-8")
        transaction.commit({**(generated or {}), f"{payload['id']}.json": str(version_stage),
                            "timeline.jsonl": str(timeline_stage)}, destination_root=self.root, validate=validate)

    def create(self, label: str, objects: list[EngineeringObject] | EngineeringProject, parent: str | None = None) -> dict[str, Any]:
        assets = objects.assets if isinstance(objects, EngineeringProject) else objects
        version_id = str(uuid.uuid4())
        payload = {"id": version_id, "label": label, "parent": parent, "created_at": _now(), "objects": [asset.to_dict() for asset in assets]}
        with RunTransaction(self.project_dir) as txn:
            txn.begin({"operation": "project.version", "label": label, "parent": parent})
            self._save(txn, payload)
        return payload

    def ingest_and_create(self, label: str, paths: list[str | Path], parent: str | None = None) -> dict[str, Any]:
        if not isinstance(paths, list):
            raise ValueError("VERSION_PATHS_REQUIRED")
        if not paths:
            # Existing clients use empty versions as named project checkpoints.
            # They have no original files to download or export.
            return self.create(label, [], parent)
        primary = list(dict.fromkeys(self._relative(path) for path in paths))
        version_id = str(uuid.uuid4())
        with RunTransaction(self.project_dir) as txn:
            txn.begin({"operation": "project.version", "label": label, "parent": parent})
            if parent is not None:
                self._load(parent)
            expected = {}
            for path in primary:
                for relative in [path, *_companions(path)]:
                    expected[relative] = file_hash(confined_path(self.project_dir, relative))
                if expected[path] is None:
                    raise FileNotFoundError(path)
            generated, files = {}, []
            for relative, digest in expected.items():
                source = confined_path(self.project_dir, relative)
                staged = confined_path(txn.stage_dir, "files/" + relative)
                size = 0
                if digest is not None:
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, staged)
                    if file_hash(staged) != digest:
                        raise RuntimeError("VERSION_SOURCE_CONFLICT:" + relative)
                    size = staged.stat().st_size
                    generated[f"{version_id}/files/{relative}"] = str(staged)
                files.append({"path": relative, "sha256": digest, "bytes": size, "exists": digest is not None})
            objects = []
            for relative in primary:
                staged = txn.stage_dir / "files" / relative
                try:
                    asset = ArtifactReader().read(staged)
                    json.dumps(asset.to_dict(), allow_nan=False)
                except Exception as exc:
                    # A failed engineering result is still valuable evidence.
                    # Preserve its bytes while making parsing failure explicit.
                    entry = next(row for row in files if row["path"] == relative)
                    if file_hash(staged) != entry["sha256"]:
                        raise RuntimeError("VERSION_STAGED_FILE_CHANGED:" + relative) from exc
                    asset = EngineeringObject(object_id=str(uuid.uuid4()), name=staged.stem, kind=AssetKind.UNKNOWN,
                        source=ArtifactRef(str(staged), entry["sha256"], entry["bytes"], ArtifactReader._media_type(staged.suffix.lower())),
                        metadata={"parse_status": "unavailable", "parse_error": str(exc)[:2000], "format": staged.suffix.lstrip(".").upper()})
                asset.source.path = str(self.project_dir / relative)
                objects.append(asset.to_dict())
            payload = {"schema_version": 2, "storage": "original_files", "id": version_id, "label": label,
                       "parent": parent, "created_at": _now(), "asset_paths": primary, "files": files, "objects": objects}
            def check_sources():
                for relative, digest in expected.items():
                    if file_hash(confined_path(self.project_dir, relative)) != digest:
                        raise RuntimeError("VERSION_SOURCE_CONFLICT:" + relative)
            check_sources()
            def validate(_committed):
                check_sources()
                self._verify_files(payload)
            self._save(txn, payload, generated, validate)
        return payload

    def list(self) -> list[dict[str, Any]]:
        with RunTransaction.read_lock(self.project_dir):
            return [self._load(path.stem) for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)]

    def load(self, version_id: str) -> dict[str, Any]:
        with RunTransaction.read_lock(self.project_dir):
            return self._load(version_id)

    def _load(self, version_id):
        path = confined_path(self.root, self._identifier(version_id) + ".json")
        if not path.is_file():
            raise FileNotFoundError(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("id") != version_id or value.get("schema_version", 1) not in {1, 2}:
            raise ValueError("VERSION_INVALID_MANIFEST")
        if value.get("schema_version") == 2:
            files = value.get("files", [])
            paths = value.get("asset_paths", [])
            if (value.get("storage") != "original_files" or not paths or len(paths) != len(value.get("objects", []))
                    or len(set(paths)) != len(paths) or not isinstance(files, list)):
                raise ValueError("VERSION_INVALID_MANIFEST")
            expected = {item for primary in paths for item in [primary, *_companions(primary)]}
            if len(files) != len(expected) or {entry.get("path") for entry in files} != expected:
                raise ValueError("VERSION_INVALID_FILE_GROUP")
            for entry in files:
                confined_path(self.root, f"{version_id}/files/{entry['path']}")
                digest = entry.get("sha256")
                if (entry.get("exists") is not (digest is not None) or (digest is not None and
                        (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)))
                        or not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0
                        or (digest is None and (entry["path"] in paths or entry["bytes"] != 0))):
                    raise ValueError("VERSION_INVALID_MANIFEST")
        return value

    def _verify_files(self, snapshot, selected=None):
        if snapshot.get("schema_version") != 2:
            raise ValueError("VERSION_RAW_FILES_UNAVAILABLE")
        for entry in snapshot["files"]:
            if selected is not None and entry["path"] not in selected:
                continue
            path = confined_path(self.root, f"{snapshot['id']}/files/{entry['path']}")
            if file_hash(path) != entry["sha256"] or (entry["exists"] and path.stat().st_size != entry["bytes"]):
                raise RuntimeError("VERSION_SNAPSHOT_CORRUPT:" + entry["path"])

    @contextmanager
    def open_file(self, version_id, relative):
        """Keep a verified descriptor and the project lock throughout a download."""
        with RunTransaction.read_lock(self.project_dir):
            snapshot = self._load(version_id)
            if snapshot.get("schema_version") != 2:
                raise ValueError("VERSION_RAW_FILES_UNAVAILABLE")
            entry = next((row for row in snapshot["files"] if row["path"] == relative and row["exists"]), None)
            if entry is None:
                raise FileNotFoundError("VERSION_FILE_NOT_FOUND")
            path = confined_path(self.root, f"{version_id}/files/{relative}")
            if not path.is_file():
                raise RuntimeError("VERSION_SNAPSHOT_CORRUPT:" + relative)
            with path.open("rb") as stream:
                digest, size = hashlib.sha256(), 0
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk); size += len(chunk)
                if digest.hexdigest() != entry["sha256"] or size != entry["bytes"]:
                    raise RuntimeError("VERSION_SNAPSHOT_CORRUPT:" + relative)
                stream.seek(0)
                yield stream, entry

    def preview(self, version_id, asset_index=0):
        from .viewer import _asset_view
        with RunTransaction.read_lock(self.project_dir):
            snapshot = self._load(version_id)
            if snapshot.get("schema_version") != 2:
                raise ValueError("VERSION_RAW_FILES_UNAVAILABLE")
            if not isinstance(asset_index, int) or not 0 <= asset_index < len(snapshot["asset_paths"]):
                raise ValueError("VERSION_ASSET_NOT_FOUND")
            relative = snapshot["asset_paths"][asset_index]
            self._verify_files(snapshot, {relative, *_companions(relative)})
            try:
                if snapshot["objects"][asset_index].get("metadata", {}).get("parse_status") == "unavailable":
                    raise ValueError("文件内容暂时无法解析，原始文件已完整保存。")
                result = _asset_view(self.root / version_id / "files", relative, read_only=True)
            except Exception as exc:
                # Rendering limits must not hide download/export controls. Raw
                # integrity checks remain outside this fallback and still fail.
                result = {"asset": snapshot["objects"][asset_index], "viewer": "", "preview_unavailable": True,
                          "preview_error": str(exc)[:2000], "preview_note": "预览不可用，原始文件可下载或导出。",
                          "svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 700 120'><text x='20' y='55'>预览不可用，原始文件可下载或导出。</text><text x='20' y='85'>" + escape(str(exc)[:80]) + "</text></svg>"}
            self._verify_files(snapshot, {relative, *_companions(relative)})
            return result

    def export(self, version_id, output_dir):
        destination = confined_path(self.project_dir, self._relative(output_dir))
        with RunTransaction(self.project_dir) as txn:
            txn.begin({"operation": "project.version_export", "version_id": version_id, "output_dir": str(output_dir)})
            if destination.exists():
                raise FileExistsError("VERSION_EXPORT_DESTINATION_EXISTS")
            snapshot = self._load(version_id)
            self._verify_files(snapshot)
            generated = {}
            for entry in snapshot["files"]:
                if entry["exists"]:
                    source = confined_path(self.root, f"{version_id}/files/{entry['path']}")
                    staged = confined_path(txn.stage_dir, "export/" + entry["path"])
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, staged)
                    if file_hash(staged) != entry["sha256"]:
                        raise RuntimeError("VERSION_SNAPSHOT_CORRUPT:" + entry["path"])
                    generated[entry["path"]] = str(staged)
            def validate(_committed):
                for entry in snapshot["files"]:
                    target = confined_path(destination, entry["path"])
                    if file_hash(target) != entry["sha256"]:
                        raise RuntimeError("VERSION_EXPORT_VALIDATION_FAILED:" + entry["path"])
            expected = {str((destination / relative).relative_to(self.project_dir)): None for relative in generated}
            written = txn.commit(generated, destination_root=destination, validate=validate, expected_before=expected)
            return {"version_id": version_id, "output_dir": str(destination), "files": written,
                    "validation": {"valid": True, "hashes_checked": len(snapshot["files"])}, "run_id": txn.run_id}

    def diff(self, left: str, right: str) -> dict[str, Any]:
        before = self.load(left)
        after = self.load(right)
        # Storage metadata is separate from the semantic engineering diff, so
        # legacy semantic snapshots remain comparable with file-backed versions.
        ignore = {"schema_version", "storage", "asset_paths", "files"}
        changes = self._diff_value({k: v for k, v in before.items() if k not in ignore},
                                   {k: v for k, v in after.items() if k not in ignore})
        return {"left": left, "right": right, "change_count": len(changes), "changes": changes}

    def timeline(self) -> list[dict[str, Any]]:
        with RunTransaction.read_lock(self.project_dir):
            path = confined_path(self.root, "timeline.jsonl")
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
                left_keys = [self._asset_key(item, index) for index, item in enumerate(before)]
                right_keys = [self._asset_key(item, index) for index, item in enumerate(after)]
                if len(set(left_keys)) == len(left_keys) and len(set(right_keys)) == len(right_keys) and (set(left_keys) != set(range(len(before))) or set(right_keys) != set(range(len(after)))):
                    changes = []
                    for key in sorted(set(left_keys) | set(right_keys)):
                        if key not in left_keys:
                            changes.append({"path": f"{path}[{key}]", "kind": "added", "after": after[right_keys.index(key)]})
                        elif key not in right_keys:
                            changes.append({"path": f"{path}[{key}]", "kind": "removed", "before": before[left_keys.index(key)]})
                        else:
                            changes.extend(self._diff_value(before[left_keys.index(key)], after[right_keys.index(key)], f"{path}[{key}]"))
                    return changes
                changes = []
                for index, (left_item, right_item) in enumerate(zip(before, after)):
                    changes.extend(self._diff_value(left_item, right_item, f"{path}[{index}]"))
                return changes
            return [{"path": path, "kind": "changed", "before": before, "after": after}]
        return [] if before == after else [{"path": path, "kind": "changed", "before": before, "after": after}]

    @staticmethod
    def _asset_key(item: dict[str, Any], index: int) -> str | int:
        source = item.get("source") or {}
        return str(source.get("path") or item.get("name") or index)

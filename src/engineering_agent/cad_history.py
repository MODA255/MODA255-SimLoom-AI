"""Durable, byte-exact CAD undo/redo committed with the drawing and sidecar."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .transactions import RunTransaction, confined_path, file_hash, _now


class CadHistory:
    def __init__(self, transaction: RunTransaction, drawing: Path):
        if transaction.lock_fd is None:
            raise RuntimeError("CAD_HISTORY_REQUIRES_PROJECT_LOCK")
        self.transaction = transaction
        self.relative = str(drawing.relative_to(transaction.project_dir))
        key = hashlib.sha256(self.relative.encode("utf-8")).hexdigest()
        self.relative_history = f".engineering-agent/cad-history/{key}.json"
        self.path = confined_path(transaction.project_dir, self.relative_history)
        self.data: dict[str, Any] = {"schema_version": 1, "path": self.relative, "cursor": 0, "revisions": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if (self.data.get("schema_version") != 1 or self.data.get("path") != self.relative
                    or not isinstance(self.data.get("cursor"), int)
                    or not 0 <= self.data["cursor"] <= len(self.data.get("revisions", []))):
                raise ValueError("CAD_HISTORY_INVALID")

    def summary(self) -> dict[str, Any]:
        cursor, revisions = self.data["cursor"], self.data["revisions"]
        return {"path": self.relative, "cursor": cursor, "can_undo": cursor > 0,
                "can_redo": cursor < len(revisions), "revisions": [
                    {"id": row["id"], "operation": row["operation"], "at": row["at"], "applied": index < cursor}
                    for index, row in enumerate(revisions)]}

    def _check(self, files: list[dict[str, Any]], side: str):
        for entry in files:
            path = confined_path(self.transaction.project_dir, entry["path"])
            if file_hash(path) != entry[side]:
                raise RuntimeError("CAD_HISTORY_CONFLICT:" + entry["path"])

    def snapshot(self, revision_id: str, side: str = "after") -> dict[str, Any]:
        """Locate and verify an immutable DXF/sidecar pair without moving the cursor.

        Only revisions in this drawing's active history branch are addressable.
        Paths are derived from the revision, never accepted from a caller or
        trusted from editable snapshot metadata.
        """
        if side not in {"before", "after"}:
            raise ValueError("CAD_HISTORY_INVALID_SIDE")
        if not isinstance(revision_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", revision_id):
            raise ValueError("CAD_HISTORY_INVALID_REVISION")
        row = next((item for item in self.data["revisions"] if item["id"] == revision_id), None)
        if row is None:
            raise ValueError("CAD_HISTORY_REVISION_NOT_FOUND:" + revision_id)
        group = (self.relative, self.relative + ".cad.json")
        entries = row.get("files", [])
        if len(entries) != 2 or {item.get("path") for item in entries} != set(group):
            raise ValueError("CAD_HISTORY_INVALID_FILE_GROUP")
        folder = "backup" if side == "before" else "after"
        verified = {}
        for entry in entries:
            source = confined_path(self.transaction.project_dir,
                                   f"artifacts/runs/{revision_id}/{folder}/{entry['path']}")
            if side not in entry or file_hash(source) != entry[side]:
                raise RuntimeError("CAD_HISTORY_SNAPSHOT_CORRUPT:" + entry["path"])
            verified[entry["path"]] = {"path": source if entry[side] is not None else None,
                                        "sha256": entry[side]}
        drawing, sidecar = (verified[path] for path in group)
        if drawing["path"] is None and sidecar["path"] is not None:
            raise ValueError("CAD_HISTORY_INVALID_FILE_GROUP")
        return {"revision_id": revision_id, "operation": row["operation"], "at": row["at"],
                "side": side, "path": drawing["path"], "sha256": drawing["sha256"],
                "sidecar_sha256": sidecar["sha256"], "exists": drawing["path"] is not None}

    def _stage_history(self, files: dict[str, str | None]):
        staged = self.transaction.stage_dir / "cad-history.json"
        staged.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        files[self.relative_history] = str(staged)

    def prepare_edit(self, files: dict[str, str | None], operation: str):
        cursor, revisions = self.data["cursor"], self.data["revisions"]
        if revisions:
            previous = revisions[cursor - 1] if cursor else revisions[0]
            self._check(previous["files"], "after" if cursor else "before")
        entries = []
        for relative in (self.relative, self.relative + ".cad.json"):
            target = confined_path(self.transaction.project_dir, relative)
            if relative not in files:
                raise ValueError("CAD_HISTORY_INCOMPLETE_FILE_GROUP")
            staged = files[relative]
            entries.append({"path": relative, "before": file_hash(target),
                            "after": file_hash(Path(staged)) if staged is not None else None,
                            "backup": str(Path("backup") / relative),
                            "snapshot": str(Path("after") / relative)})
        revision = {"id": self.transaction.run_id, "operation": operation, "at": _now(), "files": entries}
        self.data["revisions"] = revisions[:cursor] + [revision]
        self.data["cursor"] = cursor + 1
        self._stage_history(files)

    def prepare_restore(self, direction: str) -> tuple[dict[str, str | None], dict[str, Any]]:
        cursor, revisions = self.data["cursor"], self.data["revisions"]
        undo = direction == "undo"
        if (undo and cursor == 0) or (not undo and cursor == len(revisions)):
            raise ValueError("CAD_NOTHING_TO_" + direction.upper())
        revision = revisions[cursor - 1 if undo else cursor]
        self._check(revision["files"], "after" if undo else "before")
        side = "before" if undo else "after"
        run = confined_path(self.transaction.project_dir, "artifacts/runs/" + revision["id"])
        files: dict[str, str | None] = {}
        for index, entry in enumerate(revision["files"]):
            if entry["path"] not in {self.relative, self.relative + ".cad.json"}:
                raise ValueError("CAD_HISTORY_INVALID_FILE_GROUP")
            source = confined_path(run, entry["backup" if undo else "snapshot"])
            if entry[side] is None:
                files[entry["path"]] = None
                continue
            if file_hash(source) != entry[side]:
                raise RuntimeError("CAD_HISTORY_SNAPSHOT_CORRUPT:" + entry["path"])
            target = self.transaction.stage_dir / "restore" / str(index)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files[entry["path"]] = str(target)
        self.data["cursor"] = cursor - 1 if undo else cursor + 1
        self._stage_history(files)
        return files, {"revision": revision["id"], "files": revision["files"], "side": side}

    def validate_restored(self, restored: dict[str, Any]):
        self._check(restored["files"], restored["side"])

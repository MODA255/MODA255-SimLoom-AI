"""Durable, polling based project file change monitoring.

The commercial applications remain the source of truth for their native files.
This module only records auditable file fingerprints so a worker can notice
when a vendor process has produced, replaced, touched, removed, or is still
writing an output file.  It deliberately uses polling rather than a platform
specific watcher: a poll can be resumed after the agent or the worker exits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .transactions import RunTransaction, confined_path


WATCH_SCHEMA_VERSION = 1
WATCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_INTERNAL_ROOTS = {".engineering-agent", ".git"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".simloom-watch-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FileWatchStore:
    """Persist project-relative file watch sessions under the project lock."""

    def __init__(self, project_dir: str | Path):
        self._input_root = Path(project_dir).expanduser().absolute()
        self.project_dir = self._input_root.resolve()
        self.root = confined_path(self.project_dir, ".engineering-agent/watchers")
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _watch_id(value: str) -> str:
        if not isinstance(value, str) or not WATCH_ID_RE.fullmatch(value):
            raise ValueError("WATCH_INVALID_ID")
        return value

    def _relative(self, value: str | Path) -> str:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("WATCH_PATH_REQUIRED")
        path = Path(value).expanduser()
        if path.is_absolute():
            if path.is_relative_to(self._input_root):
                path = path.relative_to(self._input_root)
            elif path.is_relative_to(self.project_dir):
                path = path.relative_to(self.project_dir)
            else:
                raise ValueError("WATCH_PATH_OUTSIDE_PROJECT")
        if not path.parts or path.parts[0] in _INTERNAL_ROOTS:
            raise ValueError("WATCH_INTERNAL_PATH")
        confined_path(self.project_dir, path.as_posix())
        return path.as_posix()

    def _state_path(self, watch_id: str) -> Path:
        return confined_path(self.root, self._watch_id(watch_id) + ".json")

    def _load(self, watch_id: str) -> dict[str, Any]:
        path = self._state_path(watch_id)
        if not path.is_file():
            raise FileNotFoundError("WATCH_NOT_FOUND")
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state.get("schema_version") != WATCH_SCHEMA_VERSION
                or state.get("watch_id") != watch_id
                or state.get("project_dir") != str(self.project_dir)
                or state.get("status") not in {"active", "stopped"}
                or not isinstance(state.get("paths"), list)
                or not isinstance(state.get("snapshot"), dict)):
            raise ValueError("WATCH_INVALID_STATE")
        return state

    def _fingerprint(self, relative: str) -> dict[str, Any]:
        target = confined_path(self.project_dir, relative)
        if not target.exists():
            return {"path": relative, "exists": False, "type": "missing", "stable": True}
        if target.is_symlink():
            raise ValueError("WATCH_SYMLINK_PATH:" + relative)
        if not target.is_file():
            return {"path": relative, "exists": True, "type": "directory", "stable": True}
        # Vendor applications often replace a file while writing it.  Read it
        # twice at most so a poll reports an unstable output instead of a
        # misleading half-file hash.
        last: dict[str, Any] | None = None
        for _attempt in range(2):
            before = target.stat()
            digest = hashlib.sha256()
            size = 0
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            after = target.stat()
            last = {"path": relative, "exists": True, "type": "file", "bytes": size,
                    "mtime_ns": int(after.st_mtime_ns), "sha256": digest.hexdigest(),
                    "stable": before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns}
            if last["stable"]:
                return last
        last["stable"] = False
        return last

    def _expand_paths(self, paths: list[str]) -> list[str]:
        expanded: set[str] = set()
        for value in paths:
            relative = self._relative(value)
            target = confined_path(self.project_dir, relative)
            if target.exists() and target.is_dir():
                expanded.add(relative)
                for child in target.rglob("*"):
                    if child.is_symlink():
                        continue
                    if child.is_file():
                        child_relative = child.relative_to(self.project_dir).as_posix()
                        if child_relative.split("/", 1)[0] not in _INTERNAL_ROOTS:
                            expanded.add(child_relative)
            else:
                expanded.add(relative)
        if not expanded:
            raise ValueError("WATCH_PATHS_EMPTY")
        return sorted(expanded)

    def _snapshot(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        return {relative: self._fingerprint(relative) for relative in self._expand_paths(paths)}

    @staticmethod
    def _changes(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in sorted(set(previous) | set(current)):
            before, after = previous.get(path), current.get(path)
            before_exists, after_exists = bool(before and before.get("exists")), bool(after and after.get("exists"))
            if not before_exists and after_exists:
                kind = "added"
            elif before_exists and not after_exists:
                kind = "deleted"
            elif not before_exists and not after_exists:
                continue
            elif not after.get("stable", True):
                kind = "unstable"
            elif before.get("sha256") != after.get("sha256") or before.get("bytes") != after.get("bytes"):
                kind = "modified"
            elif before.get("mtime_ns") != after.get("mtime_ns"):
                kind = "touched"
            else:
                continue
            result.append({"path": path, "change": kind, "before": before or {"exists": False}, "after": after or {"exists": False}})
        return result

    def start(self, paths: list[str], *, label: str = "") -> dict[str, Any]:
        if not isinstance(paths, list) or not paths:
            raise ValueError("WATCH_PATHS_REQUIRED")
        if len(paths) > 256:
            raise ValueError("WATCH_TOO_MANY_PATHS")
        normalized = sorted(dict.fromkeys(self._relative(value) for value in paths))
        watch_id = uuid.uuid4().hex
        state = {"schema_version": WATCH_SCHEMA_VERSION, "watch_id": watch_id,
                 "project_dir": str(self.project_dir), "label": str(label or ""),
                 "status": "active", "paths": normalized, "sequence": 0,
                 "created_at": _now(), "updated_at": _now(),
                 "snapshot": self._snapshot(normalized), "events": []}
        with RunTransaction.read_lock(self.project_dir):
            _atomic_json(self._state_path(watch_id), state)
        return self._public(state, changes=[])

    def poll(self, watch_id: str, *, since: int | None = None) -> dict[str, Any]:
        with RunTransaction.read_lock(self.project_dir):
            state = self._load(watch_id)
            if state["status"] != "active":
                return self._public(state, changes=[], stopped=True)
            current = self._snapshot(state["paths"])
            changes = self._changes(state["snapshot"], current)
            state["sequence"] = int(state.get("sequence", 0)) + 1
            event = {"sequence": state["sequence"], "observed_at": _now(), "changes": changes}
            state["events"] = [*state.get("events", []), event][-200:]
            state["snapshot"], state["updated_at"] = current, event["observed_at"]
            _atomic_json(self._state_path(watch_id), state)
            history = state["events"] if since is None else [item for item in state["events"] if item["sequence"] > int(since)]
            return self._public(state, changes=changes, history=history)

    def status(self, watch_id: str) -> dict[str, Any]:
        with RunTransaction.read_lock(self.project_dir):
            return self._public(self._load(watch_id), changes=[])

    def stop(self, watch_id: str) -> dict[str, Any]:
        with RunTransaction.read_lock(self.project_dir):
            state = self._load(watch_id)
            state["status"], state["updated_at"] = "stopped", _now()
            _atomic_json(self._state_path(watch_id), state)
            return self._public(state, changes=[], stopped=True)

    @staticmethod
    def _public(state: dict[str, Any], *, changes: list[dict[str, Any]], history: list[dict[str, Any]] | None = None,
                stopped: bool = False) -> dict[str, Any]:
        return {"schema_version": WATCH_SCHEMA_VERSION, "watch_id": state["watch_id"],
                "label": state.get("label", ""), "status": state["status"],
                "paths": state["paths"], "sequence": state.get("sequence", 0),
                "created_at": state["created_at"], "updated_at": state["updated_at"],
                "changes": changes, "history": history if history is not None else [],
                "stopped": bool(stopped or state["status"] == "stopped")}


from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunTransaction:
    """Stage generated artifacts, journal every transition, then commit once."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.run_id = str(uuid.uuid4())
        self.run_dir = self.project_dir / "artifacts" / "runs" / self.run_id
        self.stage_dir = self.run_dir / "stage"
        self.journal_path = self.run_dir / "journal.jsonl"
        self.state_path = self.run_dir / "state.json"
        self.lock_path = self.project_dir / ".engineering-agent" / "project.lock"
        self.lock_fd: int | None = None
        self.committed_files: dict[str, str] = {}
        self.state = "created"

    def begin(self, plan: dict[str, Any]):
        self._acquire_lock()
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        self.record("begin", {"plan": plan})
        self.state = "running"
        self._write_state()

    def record(self, event: str, payload: dict[str, Any] | None = None):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": _now(), "event": event, "payload": payload or {}}, ensure_ascii=False) + "\n")

    def checkpoint(self, label: str, payload: dict[str, Any] | None = None) -> str:
        checkpoint = self.run_dir / "checkpoints" / label
        checkpoint.mkdir(parents=True, exist_ok=True)
        (checkpoint / "state.json").write_text(json.dumps(payload or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.record("checkpoint", {"label": label, **(payload or {})})
        self._write_state()
        return str(checkpoint)

    def commit(self, generated: dict[str, str]) -> dict[str, str]:
        final_root = self.project_dir / "artifacts" / "generated"
        backup_root = self.run_dir / "backup"
        committed: dict[str, str] = {}
        for relative, staged in generated.items():
            source = Path(staged)
            if not source.exists():
                raise FileNotFoundError(source)
            target = final_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            shutil.copy2(source, target)
            committed[relative] = str(target)
        manifest = self.run_dir / "manifest.json"
        manifest.write_text(json.dumps(committed, ensure_ascii=False, indent=2), encoding="utf-8")
        self.record("commit", {"files": committed})
        self.state = "committed"
        self.committed_files = committed
        self._write_state()
        self.release_lock()
        return committed

    def rollback(self, reason: str):
        self.record("rollback", {"reason": reason})
        backup_root = self.run_dir / "backup"
        for relative, target_name in self.committed_files.items():
            target = Path(target_name)
            backup = backup_root / relative
            if backup.exists():
                shutil.copy2(backup, target)
            elif target.exists():
                target.unlink()
        if self.stage_dir.exists():
            shutil.rmtree(self.stage_dir)
        self.state = "rolled_back"
        self._write_state()
        self.release_lock()

    def release_lock(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _acquire_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.lock_fd, json.dumps({"run_id": self.run_id, "pid": os.getpid(), "created_at": _now()}).encode())
        except FileExistsError as exc:
            raise RuntimeError("PROJECT_LOCKED") from exc

    def _write_state(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"run_id": self.run_id, "state": self.state, "updated_at": _now(), "pid": os.getpid()}, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def recover_stale(cls, project_dir: str | Path, max_age_seconds: int = 3600) -> list[str]:
        root = Path(project_dir).resolve() / "artifacts" / "runs"
        recovered = []
        if not root.exists():
            return recovered
        now = time.time()
        for state_path in root.glob("*/state.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("state") != "running" or now - state_path.stat().st_mtime < max_age_seconds:
                    continue
                state["state"] = "abandoned"
                state["recovered_at"] = _now()
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                recovered.append(state.get("run_id", state_path.parent.name))
            except (OSError, json.JSONDecodeError):
                continue
        lock = Path(project_dir).resolve() / ".engineering-agent" / "project.lock"
        if lock.exists() and recovered:
            try:
                lock.unlink()
            except OSError:
                pass
        return recovered

    def events(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        events = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

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
        self.lock_stale_seconds = 3600

    def __enter__(self) -> "RunTransaction":
        return self

    def __exit__(self, exc_type, _exc, _tb) -> bool:
        if exc_type is not None and self.state in {"created", "running"}:
            self.fail(str(_exc or exc_type.__name__))
        elif self.state == "running":
            self.fail("transaction exited before commit")
        return False

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

    def commit(self, generated: dict[str, str], destination_root: str | Path | None = None) -> dict[str, str]:
        """Commit staged files atomically into a destination directory.

        The default destination is ``artifacts/generated``. Callers that own a
        different project-managed directory (for example version metadata) can
        pass ``destination_root`` while retaining the same journal, lock and
        rollback semantics.
        """
        final_root = Path(destination_root).resolve() if destination_root is not None else self.project_dir / "artifacts" / "generated"
        if self.project_dir not in final_root.parents and final_root != self.project_dir:
            raise ValueError("TRANSACTION_DESTINATION_OUTSIDE_PROJECT")
        backup_root = self.run_dir / "backup"
        committed: dict[str, str] = {}
        try:
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
        except Exception as exc:
            self.record("commit_failed", {"error": str(exc), "files": committed})
            self._restore_targets(committed, backup_root, final_root)
            self.state = "failed"
            self._write_state()
            self.release_lock()
            raise

    def rollback(self, reason: str):
        self.record("rollback", {"reason": reason})
        backup_root = self.run_dir / "backup"
        self._restore_targets(self.committed_files, backup_root, self.project_dir / "artifacts" / "generated")
        if self.stage_dir.exists():
            shutil.rmtree(self.stage_dir)
        self.state = "rolled_back"
        self._write_state()
        self.release_lock()

    def fail(self, reason: str):
        self.record("failed", {"reason": reason})
        self.state = "failed"
        self._write_state()
        self.release_lock()

    @staticmethod
    def _restore_targets(committed: dict[str, str], backup_root: Path, final_root: Path):
        for relative, target_name in committed.items():
            target = Path(target_name)
            backup = backup_root / relative
            try:
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                # A best-effort restore is recorded by the caller; keep the
                # original exception as the primary failure for the client.
                continue

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
            if self._lock_is_stale():
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
                self.lock_fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.lock_fd, json.dumps({"run_id": self.run_id, "pid": os.getpid(), "created_at": _now()}).encode())
                self.record("stale_lock_recovered", {})
                return
            raise RuntimeError("PROJECT_LOCKED") from exc

    def _lock_is_stale(self) -> bool:
        try:
            info = json.loads(self.lock_path.read_text(encoding="utf-8"))
            pid = int(info.get("pid", 0))
            age = time.time() - self.lock_path.stat().st_mtime
            if age > self.lock_stale_seconds:
                return True
            if pid <= 0:
                return True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            return False
        except (OSError, ValueError, json.JSONDecodeError):
            return True

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
                stage = state_path.parent / "stage"
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
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

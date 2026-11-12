from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"TRANSACTION_NOT_A_FILE:{path}")
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_directory(path: Path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".simloom-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_copy(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".simloom-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        _sync_directory(target.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_write(path: Path, value: Any):
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid process ID.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def confined_path(root: Path, relative: str) -> Path:
    """Reject traversal and symlinks, including links within the workspace."""
    part = Path(relative)
    if part.is_absolute() or not part.parts or ".." in part.parts:
        raise ValueError("TRANSACTION_PATH_OUTSIDE_PROJECT")
    target = root / part
    for component in (target, *target.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError("TRANSACTION_SYMLINK_PATH")
    if not target.resolve().is_relative_to(root):
        raise ValueError("TRANSACTION_PATH_OUTSIDE_PROJECT")
    return target


class RunTransaction:
    """Recoverable file commits serialized by an OS lock.

    Individual files are atomically replaced. A durable before/after manifest
    permits recovery of an interrupted multi-file commit. Participating readers
    must take the same project lock to observe a consistent group of files.
    """

    pending_states = {"running", "committing", "rolling_back", "recovery_failed"}

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.run_id = str(uuid.uuid4())
        self.run_dir = self.project_dir / "artifacts" / "runs" / self.run_id
        self.stage_dir = self.run_dir / "stage"
        self.journal_path = self.run_dir / "journal.jsonl"
        self.state_path = self.run_dir / "state.json"
        self.lock_path = self.project_dir / ".engineering-agent" / "project.lock"
        self.guard_path = self.lock_path.with_name("project.guard")
        self.lock_fd: int | None = None
        self.committed_files: dict[str, str] = {}
        self.changes: list[dict[str, Any]] = []
        self.state = "created"
        self.plan: dict[str, Any] = {}

    def __enter__(self) -> "RunTransaction":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if self.state in self.pending_states:
            self.fail(str(exc or "transaction exited before commit"))
        else:
            self.release_lock()
        return False

    def begin(self, plan: dict[str, Any]):
        if self.state != "created":
            raise RuntimeError("TRANSACTION_ALREADY_STARTED")
        self._acquire_lock()
        try:
            self._recover_pending()
            self.stage_dir.mkdir(parents=True, exist_ok=True)
            self.plan = plan
            self.record("begin", {"plan": plan})
            self.state = "running"
            self._write_state()
        except BaseException:
            self.release_lock()
            raise

    def record(self, event: str, payload: dict[str, Any] | None = None):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": _now(), "event": event, "payload": payload or {}}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def checkpoint(self, label: str, payload: dict[str, Any] | None = None) -> str:
        checkpoint = confined_path(self.run_dir / "checkpoints", label)
        _json_write(checkpoint / "state.json", payload or {})
        self.record("checkpoint", {"label": label, **(payload or {})})
        return str(checkpoint)

    def commit(self, generated: dict[str, str | None], destination_root: str | Path | None = None,
               *, validate: Callable[[dict[str, str]], Any] | None = None,
               expected_before: dict[str, str | None] | None = None) -> dict[str, str]:
        """Commit, validate actual files under the lock, then release it.

        A None source removes a file, enabling transactional undo of creations.
        On validation or I/O failure, restore every before-image before unlocking.
        """
        if self.state != "running" or self.lock_fd is None:
            raise RuntimeError("TRANSACTION_NOT_RUNNING")
        try:
            final_root = Path(destination_root).resolve() if destination_root is not None else self.project_dir / "artifacts/generated"
            if not final_root.is_relative_to(self.project_dir):
                raise ValueError("TRANSACTION_DESTINATION_OUTSIDE_PROJECT")
            entries = []
            targets: set[Path] = set()
            for relative, staged in generated.items():
                target = confined_path(final_root, relative)
                project_relative = str(target.relative_to(self.project_dir))
                confined_path(self.project_dir, project_relative)
                if target in targets or target in {self.lock_path, self.guard_path} or target.is_relative_to(self.project_dir / "artifacts/runs"):
                    raise ValueError("TRANSACTION_RESERVED_OR_DUPLICATE_PATH")
                targets.add(target)
                source = Path(staged).resolve() if staged is not None else None
                if source is not None and not source.is_relative_to(self.stage_dir):
                    raise ValueError("TRANSACTION_SOURCE_OUTSIDE_STAGE")
                if source is not None and not source.is_file():
                    raise FileNotFoundError(source)
                entry = {"key": relative, "path": project_relative, "before": file_hash(target),
                         "after": file_hash(source) if source is not None else None,
                         "backup": str(Path("backup") / relative), "snapshot": str(Path("after") / relative)}
                if expected_before is not None and project_relative in expected_before and entry["before"] != expected_before[project_relative]:
                    raise RuntimeError("TRANSACTION_CONFLICT:" + project_relative)
                if entry["before"] is not None:
                    _atomic_copy(target, confined_path(self.run_dir, entry["backup"]))
                if source is not None:
                    _atomic_copy(source, confined_path(self.run_dir, entry["snapshot"]))
                    if file_hash(confined_path(self.run_dir, entry["snapshot"])) != entry["after"]:
                        raise RuntimeError("TRANSACTION_SOURCE_CHANGED")
                entries.append(entry)
            # Publish intent and all snapshots durably before touching any target.
            _json_write(self.run_dir / "changes.json", {"schema_version": 1, "files": entries})
            self.changes = entries
            self.state = "committing"
            self._write_state()
            for entry in entries:
                target = confined_path(self.project_dir, entry["path"])
                if file_hash(target) != entry["before"]:
                    raise RuntimeError("TRANSACTION_CONFLICT:" + entry["path"])
                self._install(entry, target, "after")
            committed = {entry["key"]: str(confined_path(self.project_dir, entry["path"])) for entry in entries}
            if validate is not None:
                validate(committed)
            for entry in entries:
                if file_hash(confined_path(self.project_dir, entry["path"])) != entry["after"]:
                    raise RuntimeError("TRANSACTION_CONFLICT:" + entry["path"])
            _json_write(self.run_dir / "manifest.json", committed)
            self.record("commit", {"files": committed})
            self.committed_files = committed
            self.state = "committed"
            self._write_state()
            return committed
        except BaseException as exc:
            self._abort(str(exc), "failed")
            raise
        finally:
            self.release_lock()

    def _install(self, entry: dict[str, Any], target: Path, side: str):
        if entry[side] is None:
            if target.exists():
                target.unlink()
                _sync_directory(target.parent)
        else:
            source = confined_path(self.run_dir, entry["backup" if side == "before" else "snapshot"])
            if file_hash(source) != entry[side]:
                raise RuntimeError("TRANSACTION_SNAPSHOT_CORRUPT:" + entry["path"])
            _atomic_copy(source, target)

    def _restore(self):
        # Check the entire group before restoring anything; never overwrite a
        # third party's changes or trust a damaged backup.
        for entry in self.changes:
            target = confined_path(self.project_dir, entry["path"])
            if file_hash(target) not in {entry["before"], entry["after"]}:
                raise RuntimeError("TRANSACTION_RECOVERY_CONFLICT:" + entry["path"])
            if entry["before"] is not None:
                backup = confined_path(self.run_dir, entry["backup"])
                if file_hash(backup) != entry["before"]:
                    raise RuntimeError("TRANSACTION_SNAPSHOT_CORRUPT:" + entry["path"])
        for entry in reversed(self.changes):
            target = confined_path(self.project_dir, entry["path"])
            if file_hash(target) != entry["before"]:
                self._install(entry, target, "before")

    def _abort(self, reason: str, terminal: str):
        try:
            if self.changes:
                self.state = "rolling_back"
                self._write_state()
                self._restore()
            self.state = terminal
            self.record(terminal, {"reason": reason})
            self._write_state()
        except Exception as exc:
            self.state = "recovery_failed"
            self._write_state()
            raise RuntimeError(f"TRANSACTION_RECOVERY_FAILED:{exc}") from exc
        finally:
            self.release_lock()

    def rollback(self, reason: str):
        if self.state in {"failed", "rolled_back"}:
            return
        if self.lock_fd is None:
            self._acquire_lock()
            try:
                self._recover_pending()
                for entry in self.changes:
                    if file_hash(confined_path(self.project_dir, entry["path"])) != entry["after"]:
                        raise RuntimeError("TRANSACTION_CONFLICT:" + entry["path"])
            except Exception:
                self.release_lock()
                raise
        self._abort(reason, "rolled_back")

    def fail(self, reason: str):
        if self.state not in {"committed", "failed", "rolled_back", "recovered", "recovery_failed"}:
            self._abort(reason, "failed")

    def _acquire_lock(self):
        if self.lock_fd is not None:
            raise RuntimeError("TRANSACTION_LOCK_ALREADY_HELD")
        confined_path(self.project_dir, str(self.guard_path.relative_to(self.project_dir)))
        confined_path(self.project_dir, str(self.lock_path.relative_to(self.project_dir)))
        self.guard_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.guard_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError("PROJECT_LOCKED") from exc
        self.lock_fd = fd
        try:
            # Also respect an older runtime's live exclusive-create lock. Its
            # age never overrides a living owner (long simulations are normal).
            if self.lock_path.exists():
                try:
                    previous = json.loads(self.lock_path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    previous = {}
                if _alive(int(previous.get("pid", 0))):
                    raise RuntimeError("PROJECT_LOCKED")
            _json_write(self.lock_path, {"run_id": self.run_id, "pid": os.getpid(), "created_at": _now()})
        except BaseException:
            self.release_lock()
            raise

    def release_lock(self):
        if self.lock_fd is None:
            return
        try:
            try:
                info = json.loads(self.lock_path.read_text(encoding="utf-8"))
                if info.get("run_id") == self.run_id:
                    self.lock_path.unlink()
            except (FileNotFoundError, ValueError):
                pass
        finally:
            fd, self.lock_fd = self.lock_fd, None
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            os.close(fd)

    def _write_state(self):
        _json_write(self.state_path, {"run_id": self.run_id, "state": self.state,
                                    "updated_at": _now(), "pid": os.getpid(), "plan": self.plan})

    def _recover_pending(self, max_age_seconds: int = 0) -> list[str]:
        recovered = []
        root = confined_path(self.project_dir, "artifacts/runs")
        if not root.exists():
            return recovered
        for state_path in sorted(root.glob("*/state.json")):
            if state_path == self.state_path:
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("state") not in self.pending_states:
                continue
            confined_path(self.project_dir, str(state_path.relative_to(self.project_dir)))
            if state.get("state") != "recovery_failed" and _alive(int(state.get("pid", 0))):
                raise RuntimeError("PROJECT_LOCKED")
            if time.time() - state_path.stat().st_mtime < max_age_seconds:
                continue
            old = RunTransaction(self.project_dir)
            old.run_id = state_path.parent.name
            old.run_dir = state_path.parent
            old.state_path = state_path
            old.journal_path = old.run_dir / "journal.jsonl"
            old.plan = state.get("plan", {})
            changes_path = old.run_dir / "changes.json"
            if changes_path.exists():
                manifest = json.loads(changes_path.read_text(encoding="utf-8"))
                if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
                    raise RuntimeError("TRANSACTION_RECOVERY_MANIFEST_INVALID")
                old.changes = manifest["files"]
            elif state["state"] != "running" or (old.run_dir / "backup").exists() or (old.run_dir / "manifest.json").exists():
                raise RuntimeError("TRANSACTION_RECOVERY_MANIFEST_MISSING:" + old.run_id)
            try:
                old._restore()
                old.state = "recovered"
                old.record("recovered", {"reason": "interrupted transaction restored before-images"})
                old._write_state()
            except Exception:
                old.state = "recovery_failed"
                old._write_state()
                raise
            recovered.append(old.run_id)
        return recovered

    @classmethod
    @contextmanager
    def read_lock(cls, project_dir: str | Path):
        """Read a consistent file group without creating a history entry."""
        transaction = cls(project_dir)
        transaction._acquire_lock()
        try:
            transaction._recover_pending()
            yield transaction
        finally:
            transaction.release_lock()

    @classmethod
    def recover_stale(cls, project_dir: str | Path, max_age_seconds: int = 3600) -> list[str]:
        transaction = cls(project_dir)
        transaction._acquire_lock()
        try:
            return transaction._recover_pending(max_age_seconds)
        finally:
            transaction.release_lock()

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

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
import re


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


def portable_relative(value: str | Path) -> str:
    """Return a project-relative path using the manifest's POSIX separators."""

    return str(value).replace("\\", "/")


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
    root = Path(root).resolve()
    part = Path(portable_relative(relative))
    if part.is_absolute() or not part.parts or ".." in part.parts:
        raise ValueError("TRANSACTION_PATH_OUTSIDE_PROJECT")
    target = root / part
    for component in (target, *target.parents):
        if component == root:
            break
        is_junction = getattr(component, "is_junction", lambda: False)()
        if component.is_symlink() or is_junction:
            raise ValueError("TRANSACTION_SYMLINK_PATH")
    # The normalized relative path has no traversal components. Avoid
    # resolving a path that may be created concurrently on Windows; its
    # extended-prefix representation can otherwise look like an escape.
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
        self._event_sequence = 0

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
        # Publish a durable queued marker before lock acquisition.  A caller
        # watching the run directory can therefore distinguish a run waiting
        # for the project lock from a process that has disappeared.
        self.plan = plan
        self.record("queued", {"plan": plan})
        self._acquire_lock()
        try:
            self._recover_pending()
            self.stage_dir.mkdir(parents=True, exist_ok=True)
            self.record("begin", {"plan": plan})
            self.record("running", {"plan": plan})
            self.state = "running"
            self._write_state()
        except BaseException:
            self.release_lock()
            raise

    def record(self, event: str, payload: dict[str, Any] | None = None):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self._event_sequence == 0 and self.journal_path.exists():
            # Recovery can reopen a journal written by an older process.  Do
            # not reset its cursor when appending the terminal event.
            for item in self._read_events_file(self.journal_path, self.run_id):
                self._event_sequence = max(self._event_sequence, int(item.get("sequence", 0)))
        self._event_sequence += 1
        name = str(event)
        phase = (payload or {}).get("phase") if isinstance(payload, dict) else None
        if phase is None:
            if name == "queued":
                phase = "queued"
            elif name in {"begin", "running"} or name.endswith("_started"):
                phase = "running"
            elif name == "checkpoint":
                phase = "checkpoint"
            elif "validation" in name or name in {"cad_diff", "diff"}:
                phase = "validation"
            elif name in {"commit", "committed", "step_committed"}:
                phase = "committed"
            elif name in {"failed", "rolled_back", "recovery_failed"} or name.endswith("_failed"):
                phase = "failed"
            else:
                phase = "info"
        record = {"schema_version": 2, "sequence": self._event_sequence,
                  "run_id": self.run_id, "at": _now(), "event": name,
                  "phase": str(phase), "payload": payload or {}}
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
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
            normalized_expected = {
                portable_relative(name): value for name, value in (expected_before or {}).items()
            }
            for relative, staged in generated.items():
                portable = portable_relative(relative)
                target = confined_path(final_root, portable)
                project_relative = target.relative_to(self.project_dir).as_posix()
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
                         "backup": portable_relative(Path("backup") / portable),
                         "snapshot": portable_relative(Path("after") / portable)}
                if project_relative in normalized_expected and entry["before"] != normalized_expected[project_relative]:
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

    @staticmethod
    def _read_events_file(path: Path, run_id: str) -> list[dict[str, Any]]:
        """Read complete JSONL records while a writer may still be appending."""
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                # A partially appended final line is intentionally ignored;
                # the next poll will see the complete record.
                continue
            if not isinstance(item, dict):
                continue
            item.setdefault("schema_version", 1)
            item.setdefault("run_id", run_id)
            item.setdefault("sequence", index)
            item.setdefault("phase", "info")
            item.setdefault("payload", {})
            events.append(item)
        return events

    def events(self, *, after: int | None = None, limit: int | None = None):
        """Return the journal, or a cursor page when ``after``/``limit`` is set.

        The no-argument form remains a list for existing reports.  The paged
        form is used by Web/MCP clients that poll a live CAD operation.
        """
        events = self._read_events_file(self.journal_path, self.run_id)
        if after is None and limit is None:
            return events
        return self._page_events(events, after=after or 0, limit=limit or 100)

    @staticmethod
    def _page_events(events: list[dict[str, Any]], *, after: int = 0, limit: int = 100) -> dict[str, Any]:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("CAD_EVENT_CURSOR_INVALID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("CAD_EVENT_LIMIT_INVALID")
        latest = max((int(item.get("sequence", 0)) for item in events), default=0)
        selected = [item for item in events if int(item.get("sequence", 0)) > after][:limit]
        next_cursor = int(selected[-1]["sequence"]) if selected else after
        has_more = any(int(item.get("sequence", 0)) > next_cursor for item in events)
        return {"schema_version": 2, "events": selected, "after": after,
                "next_cursor": next_cursor, "latest_cursor": latest,
                "has_more": has_more}

    @classmethod
    def event_page(cls, project_dir: str | Path, run_id: str, *, after: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read a live or completed transaction journal without taking the writer lock."""
        root = Path(project_dir).expanduser().resolve()
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
            raise ValueError("CAD_EVENT_INVALID_RUN_ID")
        run_dir = confined_path(root, f"artifacts/runs/{run_id}")
        if not run_dir.is_dir():
            raise FileNotFoundError("CAD_EVENT_RUN_NOT_FOUND")
        page = cls._page_events(cls._read_events_file(run_dir / "journal.jsonl", run_id), after=after, limit=limit)
        state_path = confined_path(run_dir, "state.json")
        state = "unknown"
        if state_path.is_file():
            try:
                raw = json.loads(state_path.read_text(encoding="utf-8"))
                state = str(raw.get("state", "unknown"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                state = "invalid"
        return {"run_id": run_id, "state": state, **page}

    @classmethod
    def list_runs(cls, project_dir: str | Path, *, offset: int = 0, limit: int = 50,
                  state: str | None = None) -> dict[str, Any]:
        """List durable CAD transaction runs for recovery and activity UIs."""
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("CAD_RUN_OFFSET_INVALID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("CAD_RUN_LIMIT_INVALID")
        if state is not None and (not isinstance(state, str) or not state.strip()):
            raise ValueError("CAD_RUN_STATE_INVALID")
        root = Path(project_dir).expanduser().resolve()
        runs_root = confined_path(root, "artifacts/runs")
        if not runs_root.exists():
            return {"schema_version": 2, "runs": [], "offset": offset,
                    "limit": limit, "total": 0, "next_offset": None}
        rows: list[dict[str, Any]] = []
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir() or run_dir.is_symlink():
                continue
            run_id = run_dir.name
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
                continue
            state_path = run_dir / "state.json"
            raw_state: dict[str, Any] = {}
            if state_path.is_file():
                try:
                    value = json.loads(state_path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        raw_state = value
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    raw_state = {}
            events = cls._read_events_file(run_dir / "journal.jsonl", run_id)
            first = events[0] if events else {}
            last = events[-1] if events else {}
            plan = raw_state.get("plan") if isinstance(raw_state.get("plan"), dict) else {}
            if not plan and isinstance(first.get("payload"), dict):
                plan = first["payload"].get("plan") if isinstance(first["payload"].get("plan"), dict) else {}
            current_state = str(raw_state.get("state") or ("queued" if events else "unknown"))
            if state is not None and current_state != state:
                continue
            rows.append({
                "run_id": run_id,
                "state": current_state,
                "operation": plan.get("operation"),
                "plan": plan,
                "created_at": first.get("at"),
                "updated_at": raw_state.get("updated_at") or last.get("at"),
                "event_count": len(events),
                "latest_cursor": max((int(item.get("sequence", 0)) for item in events), default=0),
                "last_event": last.get("event"),
                "last_phase": last.get("phase"),
                "last_error": (last.get("payload") or {}).get("reason") or (last.get("payload") or {}).get("error"),
            })
        rows.sort(key=lambda item: (str(item.get("updated_at") or ""), item["run_id"]), reverse=True)
        total = len(rows)
        selected = rows[offset:offset + limit]
        next_offset = offset + len(selected) if offset + len(selected) < total else None
        return {"schema_version": 2, "runs": selected, "offset": offset,
                "limit": limit, "total": total, "next_offset": next_offset}

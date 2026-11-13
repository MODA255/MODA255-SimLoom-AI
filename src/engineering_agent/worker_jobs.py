"""Durable software jobs supervised independently from the HTTP service.

A process-held lock proves supervisor ownership. A persistent software lease
survives crashes and must be explicitly released after uncertain termination.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .software_worker import run_job, validate_job
from .artifact_transfer import ArtifactTransferStore
from .worker_protocol import MAX_BYTES, SCHEMA, WorkerError, failure, file_manifest, manifest, pack_file, safe_file

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
ACTIVE = frozenset({"accepted", "queued", "running", "cancelling"})


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise WorkerError("INVALID_REQUEST_ID")
    return value


def request_digest(payload):
    keys = ("schema_version", "request_id", "tool", "action", "script", "parameters", "inputs", "outputs", "files", "validation")
    normalized = {key: payload.get(key) for key in keys}
    if "vendor_options" in payload:
        normalized["vendor_options"] = payload["vendor_options"]
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class ProcessGuard:
    """A nonblocking lock tied to an open handle, not a pid or a stale file."""
    def __init__(self, path):
        self.path, self.fd = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
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
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)

    def __enter__(self):
        if not self.acquire():
            raise WorkerError("JOB_SUPERVISOR_ALREADY_ACTIVE")
        return self

    def __exit__(self, *_args):
        self.close()


class JobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.control = safe_file(self.root, "control")
        self.control.mkdir(parents=True, exist_ok=True)
        self.database = safe_file(self.root, "control/jobs.sqlite3")
        self.transfers = ArtifactTransferStore(self.root)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    digest TEXT NOT NULL, tool TEXT NOT NULL, action TEXT NOT NULL,
                    state TEXT NOT NULL, request_json TEXT NOT NULL, config_json TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, supervisor_pid INTEGER,
                    runner_pid INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    requires_review INTEGER NOT NULL DEFAULT 0, result_json TEXT,
                    released_reason TEXT, heartbeat REAL
                );
                CREATE TABLE IF NOT EXISTS leases (tool TEXT PRIMARY KEY, job_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    state TEXT NOT NULL, at REAL NOT NULL, detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);
            ''')

    @contextmanager
    def db(self, *, write=False):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def guard(self, identifier):
        return ProcessGuard(safe_file(self.root, "control/guards/" + job_id(identifier) + ".lock"))

    @staticmethod
    def event(db, identifier, state, detail=""):
        db.execute("INSERT INTO events(job_id,state,at,detail) VALUES(?,?,?,?)", (identifier, state, time.time(), detail))

    def row(self, identifier):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id(identifier),)).fetchone()
        if row is None:
            raise WorkerError("JOB_NOT_FOUND", identifier)
        return dict(row)

    def register(self, payload, commands, timeout):
        validate_job(payload, commands, self.transfers)
        digest, identifier = request_digest(payload), payload["request_id"]
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 604800:
            raise WorkerError("INVALID_JOB_TIMEOUT")
        with self.db(write=True) as db:
            previous = db.execute("SELECT digest FROM jobs WHERE id=?", (identifier,)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise WorkerError("JOB_ID_CONFLICT", "The same request ID has different inputs or parameters")
                return False
            at = time.time()
            db.execute("INSERT INTO jobs(id,digest,tool,action,state,request_json,config_json,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                       (identifier, digest, payload["tool"], payload["action"], "accepted", json.dumps(payload, allow_nan=False),
                        json.dumps({"commands": commands, "timeout": timeout}, allow_nan=False), at, at))
            self.event(db, identifier, "accepted")
        return True

    def status(self, identifier):
        row = self.row(identifier)
        # Holding the guard while changing state excludes a delayed supervisor.
        guard = self.guard(identifier)
        if row["state"] in {"queued", "running", "cancelling"} and guard.acquire():
            try:
                with self.db(write=True) as db:
                    current = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
                    if current["state"] in {"queued", "running", "cancelling"}:
                        lease = db.execute("SELECT job_id FROM leases WHERE job_id=?", (identifier,)).fetchone()
                        result = failure(WorkerError("JOB_SUPERVISOR_LOST", "Supervisor ownership lost; no automatic restart"), identifier)
                        db.execute("UPDATE jobs SET state='interrupted',requires_review=?,result_json=?,updated=? WHERE id=?",
                                   (bool(lease), json.dumps(result), time.time(), identifier))
                        self.event(db, identifier, "interrupted", "Supervisor guard is no longer held")
                    elif current["state"] == "accepted":
                        # Never infer termination from startup age. Explicit launch is idempotent.
                        pass
            finally:
                guard.close()
            row = self.row(identifier)
        with self.db() as db:
            lease = db.execute("SELECT job_id FROM leases WHERE tool=?", (row["tool"],)).fetchone()
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        return {"schema_version": SCHEMA, "request_id": row["id"], "request_digest": row["digest"],
                "tool": row["tool"], "action": row["action"], "status": row["state"],
                "created_at": row["created"], "updated_at": row["updated"], "heartbeat": row["heartbeat"],
                "supervisor_pid": row["supervisor_pid"], "runner_pid": row["runner_pid"],
                "cancel_requested": bool(row["cancel_requested"]), "requires_review": bool(row["requires_review"]),
                "software_lease": lease["job_id"] if lease else None,
                "outputs": result.get("outputs", []), "error": result.get("error"),
                "released_reason": row["released_reason"], "vendor_validation": result.get("vendor_validation", "not_run"),
                "verification": result.get("verification", "not_run")}

    def cancel(self, identifier):
        self.row(identifier)
        with self.db(write=True) as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row["state"] not in TERMINAL:
                if row["state"] == "accepted":
                    db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,updated=? WHERE id=?", (time.time(), identifier))
                    self.event(db, identifier, "cancelled", "Cancelled before supervisor started")
                else:
                    db.execute("UPDATE jobs SET cancel_requested=1,updated=? WHERE id=?", (time.time(), identifier))
                    self.event(db, identifier, "cancel_requested")
        return self.status(identifier)

    def release(self, identifier, confirmed_stopped, reason):
        if confirmed_stopped is not True or not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise WorkerError("SOFTWARE_STOP_CONFIRMATION_REQUIRED")
        guard = self.guard(identifier)
        if not guard.acquire():
            raise WorkerError("JOB_STILL_ACTIVE", "Cannot release a software lease held by a live supervisor")
        try:
            with self.db(write=True) as db:
                row = db.execute("SELECT state FROM jobs WHERE id=?", (identifier,)).fetchone()
                if not row:
                    raise WorkerError("JOB_NOT_FOUND")
                if row["state"] not in TERMINAL:
                    raise WorkerError("JOB_NOT_TERMINAL")
                db.execute("DELETE FROM leases WHERE job_id=?", (identifier,))
                db.execute("UPDATE jobs SET requires_review=0,released_reason=?,updated=? WHERE id=?", (reason, time.time(), identifier))
                self.event(db, identifier, "software_released", reason)
        finally:
            guard.close()
        return self.status(identifier)

    def list(self, *, limit=20, before=None, tool=None):
        if type(limit) is not int or not 1 <= limit <= 100 or (before is not None and (type(before) is not int or before < 1)):
            raise WorkerError("INVALID_JOB_PAGE")
        if tool is not None and tool not in {"catia", "ansys", "adams", "vi-grade"}:
            raise WorkerError("INVALID_SOFTWARE_ACTION")
        with self.db() as db:
            rows = db.execute("SELECT seq,id FROM jobs WHERE seq<? AND (? IS NULL OR tool=?) ORDER BY seq DESC LIMIT ?",
                              (before or 9223372036854775807, tool, tool, limit + 1)).fetchall()
        return {"schema_version": SCHEMA, "jobs": [self.status(row["id"]) for row in rows[:limit]],
                "next_cursor": rows[limit - 1]["seq"] if len(rows) > limit else None}

    def logs(self, identifier, *, stream="stdout", offset=0, limit=16384):
        row = self.row(identifier)
        if stream not in {"stdout", "stderr"} or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536:
            raise WorkerError("INVALID_LOG_PAGE")
        # safe_file rejects reserved paths only at the top level, protecting all symlinks.
        path = safe_file(self.root, f"jobs/{row['id']}/_logs/{stream}.txt")
        if not path.exists():
            content, next_offset, size = b"", 0, 0
        else:
            with path.open("rb") as handle:
                size = path.stat().st_size
                if offset > size:
                    raise WorkerError("LOG_OFFSET_OUT_OF_RANGE")
                handle.seek(offset)
                content = handle.read(limit)
                next_offset = handle.tell()
        return {"schema_version": SCHEMA, "request_id": identifier, "stream": stream,
                "text": content.decode("utf-8", errors="replace"), "content_base64": base64.b64encode(content).decode("ascii"), "next_offset": next_offset,
                "bytes": size, "eof": next_offset >= size, "status": self.status(identifier)["status"]}

    def events(self, identifier, *, after=0, limit=100):
        self.row(identifier)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise WorkerError("INVALID_EVENT_PAGE")
        with self.db() as db:
            rows = db.execute("SELECT seq,state,at,detail FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (identifier, after, limit + 1)).fetchall()
        return {"schema_version": SCHEMA, "request_id": identifier, "events": [dict(r) for r in rows[:limit]],
                "next_cursor": rows[min(len(rows), limit) - 1]["seq"] if rows else after,
                "has_more": len(rows) > limit}

    def result(self, identifier):
        status = self.status(identifier)
        if status["status"] not in TERMINAL:
            raise WorkerError("JOB_NOT_FINISHED")
        row = self.row(identifier)
        result = json.loads(row["result_json"]) if row["result_json"] else failure(WorkerError("JOB_CANCELLED"), identifier)
        if status["status"] == "completed":
            records = []
            root = safe_file(self.root, "jobs/" + identifier)
            for receipt in result["outputs"]:
                target = safe_file(root, receipt["path"])
                current = file_manifest(target, receipt["path"])
                if current != receipt:
                    raise WorkerError("RESULT_CHANGED_AFTER_COMPLETION", receipt["path"])
                if current["bytes"] <= MAX_BYTES:
                    record = pack_file(root, receipt["path"])
                else:
                    record = {**current, "transfer_required": True}
                records.append(record)
            result["outputs"] = records
        return result | {"request_digest": row["digest"], "job_status": status["status"],
                         "requires_review": status["requires_review"]}

    def output_path(self, identifier, path):
        """Return a completed output path for bounded chunk download."""
        identifier = job_id(identifier)
        status = self.status(identifier)
        if status["status"] != "completed":
            raise WorkerError("JOB_NOT_COMPLETED")
        row = self.row(identifier)
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        expected = next((item for item in result.get("outputs", []) if item.get("path") == path), None)
        if expected is None:
            raise WorkerError("OUTPUT_NOT_DECLARED", str(path))
        target = safe_file(self.root, "jobs/" + identifier + "/" + path)
        if not target.is_file():
            raise WorkerError("ARTIFACT_MISSING", str(path))
        actual = file_manifest(target, path)
        if actual != {key: expected[key] for key in ("path", "bytes", "sha256")}:
            raise WorkerError("RESULT_CHANGED_AFTER_COMPLETION", str(path))
        return target


class JobManager:
    def __init__(self, root, commands, timeout=1800):
        self.root, self.commands, self.timeout = Path(root).resolve(), commands, timeout
        self._store = None
        self._children = {}
        self._launch_lock = threading.Lock()

    @property
    def store(self):
        if self._store is None:
            self._store = JobStore(self.root)
        return self._store

    def submit(self, payload):
        self.store.register(payload, self.commands, self.timeout)
        identifier = payload["request_id"]
        self._ensure_supervisor(identifier)
        return self.store.status(identifier)

    def _ensure_supervisor(self, identifier):
        # Only durable accepted work may be launched; running/uncertain work is never restarted.
        if self.store.row(identifier)["state"] == "accepted":
            with self._launch_lock:
                child = self._children.get(identifier)
                if child is None or child.poll() is not None:
                    self._children[identifier] = self._spawn(identifier)

    def _spawn(self, identifier):
        # Multiple launch attempts are safe: only the supervisor holding the guard
        # can transition accepted -> queued. The loser never starts a vendor runner.
        log = safe_file(self.root, "control/supervisor.log")
        env = dict(os.environ)
        package_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            with log.open("ab") as output:
                process = subprocess.Popen([sys.executable, "-m", "engineering_agent.worker_supervisor", str(self.root), identifier],
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                           env=env, close_fds=True, **options)
            # Reap children while this service lives; this thread does not own execution.
            threading.Thread(target=process.wait, daemon=True).start()
            return process
        except OSError as exc:
            raise WorkerError("SUPERVISOR_START_FAILED", str(exc)) from exc

    def handle(self, payload):
        operation = payload.get("operation")
        if operation == "job.submit":
            return self.submit(payload)
        if operation == "job.list":
            return self.store.list(limit=payload.get("limit", 20), before=payload.get("cursor"), tool=payload.get("tool"))
        identifier = job_id(payload.get("request_id"))
        if operation == "job.status":
            self._ensure_supervisor(identifier)
            return self.store.status(identifier)
        if operation == "job.result":
            return self.store.result(identifier)
        if operation == "job.cancel":
            return self.store.cancel(identifier)
        if operation == "job.release":
            return self.store.release(identifier, payload.get("confirmed_stopped"), payload.get("reason"))
        if operation == "job.logs":
            return self.store.logs(identifier, stream=payload.get("stream", "stdout"), offset=payload.get("offset", 0), limit=payload.get("limit", 16384))
        if operation == "job.events":
            return self.store.events(identifier, after=payload.get("after", 0), limit=payload.get("limit", 100))
        raise WorkerError("UNKNOWN_JOB_OPERATION")


def supervise(root, identifier):
    store = JobStore(root)
    guard = store.guard(identifier)
    if not guard.acquire():
        return
    try:
        row = store.row(identifier)
        if row["state"] != "accepted":
            return
        with store.db(write=True) as db:
            changed = db.execute("UPDATE jobs SET state='queued',supervisor_pid=?,updated=?,heartbeat=? WHERE id=? AND state='accepted'",
                                 (os.getpid(), time.time(), time.time(), identifier)).rowcount
            if not changed:
                return
            store.event(db, identifier, "queued")
        last_heartbeat = 0
        while True:
            with store.db(write=True) as db:
                current = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (identifier,)).fetchone()
                if current["cancel_requested"]:
                    db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=?", (time.time(), identifier))
                    store.event(db, identifier, "cancelled", "Cancelled while waiting for software")
                    return
                lease = db.execute("SELECT job_id FROM leases WHERE tool=?", (row["tool"],)).fetchone()
                head = db.execute("SELECT id FROM jobs WHERE tool=? AND state IN ('accepted','queued') ORDER BY seq LIMIT 1", (row["tool"],)).fetchone()
                if not lease and head and head["id"] == identifier:
                    db.execute("INSERT INTO leases(tool,job_id) VALUES(?,?)", (row["tool"], identifier))
                    db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(), identifier))
                    store.event(db, identifier, "running")
                    break
                if time.monotonic() - last_heartbeat >= 1:
                    db.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), identifier))
                    last_heartbeat = time.monotonic()
            time.sleep(0.2)

        def monitor(pid):
            nonlocal last_heartbeat
            with store.db(write=True) as db:
                current = db.execute("SELECT cancel_requested,runner_pid,state FROM jobs WHERE id=?", (identifier,)).fetchone()
                if pid and current["runner_pid"] != pid:
                    db.execute("UPDATE jobs SET runner_pid=?,updated=? WHERE id=?", (pid, time.time(), identifier))
                    store.event(db, identifier, "runner_started", str(pid))
                if time.monotonic() - last_heartbeat >= 1:
                    db.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), identifier))
                    last_heartbeat = time.monotonic()
                if current["cancel_requested"] and current["state"] != "cancelling":
                    db.execute("UPDATE jobs SET state='cancelling',updated=? WHERE id=?", (time.time(), identifier))
                    store.event(db, identifier, "cancelling")
                return bool(current["cancel_requested"])

        payload, config = json.loads(row["request_json"]), json.loads(row["config_json"])
        result = run_job(store.root, payload, config["commands"], timeout=config["timeout"], monitor=monitor,
                         transfer_store=store.transfers)
        if result["status"] == "completed":
            result["outputs"] = manifest(result["outputs"])
        code = result.get("error", {}).get("code")
        state = "cancelled" if code == "JOB_CANCELLED" else result["status"]
        current = store.row(identifier)
        # A failed runner can leave an external software session alive. Preserve the lease.
        requires_review = result["status"] != "completed" and (bool(current["runner_pid"]) or result.get("requires_review", True))
        result["requires_review"] = requires_review
        with store.db(write=True) as db:
            db.execute("UPDATE jobs SET state=?,result_json=?,requires_review=?,updated=?,heartbeat=? WHERE id=?",
                       (state, json.dumps(result), requires_review, time.time(), time.time(), identifier))
            if not requires_review:
                db.execute("DELETE FROM leases WHERE job_id=?", (identifier,))
            store.event(db, identifier, state, code or "")
    finally:
        guard.close()



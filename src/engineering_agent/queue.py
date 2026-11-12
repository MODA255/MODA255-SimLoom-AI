from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskQueue:
    def __init__(self, path: str | Path = "engineering-agent.db"):
        self.path = str(path)
        self._lock = threading.Lock()
        self._init()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self):
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project_id TEXT, prompt TEXT NOT NULL, project_dir TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT, created_at TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            for name, declaration in (("project_dir", "TEXT"), ("metadata_json", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE projects ADD COLUMN {name} {declaration}")

            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            for name, declaration in (("mode", "TEXT NOT NULL DEFAULT 'rehearsal'"),
                                      ("input_json", "TEXT"), ("retry_of", "TEXT"), ("workflow_id", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project_id, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_retry ON tasks(retry_of, status)")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_workflow ON tasks(workflow_id)")

    def create_project(self, name: str, project_dir: str | None = None, metadata: dict[str, Any] | None = None) -> str:
        project_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO projects(id,name,created_at,project_dir,metadata_json) VALUES (?, ?, ?, ?, ?)",
                       (project_id, name, _now(), project_dir, json.dumps(metadata or {}, ensure_ascii=False)))
        return project_id

    def list_projects(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._project(row) if row else None

    @staticmethod
    def _project(row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
        return value

    def submit(self, prompt: str, project_dir: str = ".", project_id: str | None = None,
               *, mode: str = "rehearsal", inputs: dict | None = None) -> str:
        if mode not in {"offline", "rehearsal"}:
            raise ValueError("未知的任务模式")
        with self._lock, self._connect() as db:
            return self._insert(db, prompt, project_dir, project_id, mode, inputs)

    def _insert(self, db, prompt, project_dir, project_id, mode, inputs, retry_of=None):
        task_id, at = str(uuid.uuid4()), _now()
        db.execute("INSERT INTO tasks(id,project_id,prompt,project_dir,status,created_at,updated_at,mode,input_json,retry_of) "
                   "VALUES (?,?,?,?,'queued',?,?,?,?,?)",
                   (task_id, project_id, prompt, project_dir, at, at, mode,
                    json.dumps(inputs, ensure_ascii=False) if inputs is not None else None, retry_of))
        db.execute("INSERT INTO task_events(task_id,event,payload_json,created_at) VALUES (?, 'queued', ?, ?)",
                   (task_id, json.dumps({"retry_of": retry_of}), at))
        return task_id

    def retry(self, task_id: str) -> str:
        # SQLite serializes the check and insert across processes, not just threads.
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            if not task:
                raise FileNotFoundError("任务不存在")
            if task["status"] != "failed":
                raise ValueError("仅失败任务可以重试")
            external = db.execute("SELECT payload_json FROM task_events WHERE task_id=? AND event='execution_mode' ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
            if external and json.loads(external[0]).get("mode") == "external":
                raise ValueError("外部执行任务请通过 CLI 重新提交并明确执行模式")
            if task["input"] is None:
                raise ValueError("旧任务没有输入快照，请重新创建任务")
            existing = db.execute("SELECT id FROM tasks WHERE retry_of=? AND status IN ('queued','running')", (task_id,)).fetchone()
            if existing:
                return existing["id"]
            return self._insert(db, task["prompt"], task["project_dir"], task["project_id"],
                                task["mode"], task["input"], task_id)

    def set_input(self, task_id, inputs):
        with self._connect() as db:
            db.execute("UPDATE tasks SET input_json=? WHERE id=? AND input_json IS NULL",
                       (json.dumps(inputs, ensure_ascii=False), task_id))

    def record_event(self, task_id, event, payload):
        with self._lock, self._connect() as db:
            if event == "workflow" and payload.get("workflow_id"):
                db.execute("UPDATE tasks SET workflow_id=? WHERE id=?", (payload["workflow_id"], task_id))
                if payload.get("status") == "running":
                    db.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (_now(), task_id))
            db.execute("INSERT INTO task_events(task_id,event,payload_json,created_at) VALUES (?,?,?,?)",
                       (task_id, event, json.dumps(payload, ensure_ascii=False), _now()))

    def update(self, task_id: str, status: str, result: dict[str, Any] | None = None):
        with self._lock, self._connect() as db:
            now = _now()
            db.execute("UPDATE tasks SET status=?, result_json=?, updated_at=? WHERE id=?", (status, json.dumps(result, ensure_ascii=False) if result is not None else None, now, task_id))
            db.execute("INSERT INTO task_events(task_id,event,payload_json,created_at) VALUES (?, ?, ?, ?)", (task_id, status, json.dumps(result or {}, ensure_ascii=False), now))

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._row(row)

    def list(self, limit: int = 50, project_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if project_id:
                rows = db.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def find_workflow(self, workflow_id):
        with self._connect() as db:
            return self._row(db.execute("SELECT * FROM tasks WHERE workflow_id=? ORDER BY created_at LIMIT 1", (workflow_id,)).fetchone())

    def next_queued(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        return self._row(row)

    def claim(self, task_id: str) -> bool:
        """Atomically transition a queued task so two clients cannot execute it."""
        with self._lock, self._connect() as db:
            now = _now()
            changed = db.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=? AND status='queued'", (now, task_id)).rowcount
            if changed:
                db.execute("INSERT INTO task_events(task_id,event,payload_json,created_at) VALUES (?, 'running', '{}', ?)", (task_id, now))
            return bool(changed)

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, task_id, event, payload_json, created_at FROM task_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            values.append(item)
        return values

    @staticmethod
    def _row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
        inputs = result.pop("input_json", None)
        result["input"] = json.loads(inputs) if inputs else None
        if (result.get("result") or {}).get("workflow") == "offline_vehicle_chain":
            result["mode"] = "offline"
        return result

"""Measured execution events and immutable output receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from .validation import hash_file


def now():
    return datetime.now(timezone.utc).isoformat()


class Progress:
    def __init__(self, callback=None):
        self.callback = callback
        self.stages = []

    def emit(self, event, payload):
        if self.callback:
            self.callback(event, payload)

    @contextmanager
    def stage(self, identifier, label, tool=None):
        data = {"id": identifier, "label": label, "tool": tool,
                "status": "running", "started_at": now()}
        start = perf_counter()
        self.emit("stage_started", dict(data))
        try:
            yield data
        except Exception as exc:
            data.update(status="failed", error=str(exc))
            raise
        finally:
            if data["status"] == "running":
                data["status"] = "completed"
            data.update(finished_at=now(), duration_seconds=round(perf_counter() - start, 6))
            self.stages.append(dict(data))
            self.emit("stage_finished", dict(data))


def commit_outputs(transaction, files):
    """Commit the latest outputs and a per-run copy in the same transaction."""
    prefix = f"history/{transaction.run_id}/"
    manifest = [
        {"name": name, "path": f"artifacts/generated/{prefix}{name}",
         "bytes": Path(path).stat().st_size, "sha256": hash_file(path)}
        for name, path in files.items()
    ]
    committed = transaction.commit({**files, **{prefix + k: v for k, v in files.items()}})
    return {name: committed[name] for name in files}, manifest

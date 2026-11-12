"""Measured execution events and immutable output receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
from time import perf_counter

from .validation import hash_file
from .worker_protocol import relative_path


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


def commit_outputs(transaction, files, output_files=None, metadata_files=None, *, expected_before=None):
    """Commit the latest outputs and a per-run copy in the same transaction."""
    prefix = f"history/{transaction.run_id}/"
    manifest = [
        {"name": name, "path": f"artifacts/generated/{prefix}{name}",
         "bytes": Path(path).stat().st_size, "sha256": hash_file(path)}
        for name, path in files.items()
    ]
    published = {"artifacts/generated/" + name: path for name, path in files.items()}
    published.update({"artifacts/generated/" + prefix + name: path for name, path in files.items()})
    for name, path in (output_files or {}).items():
        relative_path(name, output=True)
        if name in published or name in files:
            raise ValueError("OUTPUT_PUBLICATION_CONFLICT:" + name)
        historical = f"artifacts/generated/{prefix}software/{name}"
        if historical in published:
            raise ValueError("OUTPUT_PUBLICATION_CONFLICT:" + historical)
        published.update({name: path, historical: path})
        manifest.append({"name": name, "path": historical, "bytes": Path(path).stat().st_size,
                         "sha256": hash_file(path)})
    for name, path in (metadata_files or {}).items():
        allowed = re.fullmatch(r"\.engineering-agent/(?:software-jobs/[0-9a-f]{32}\.json|workflows/[0-9a-f]{32}/state\.json)", name)
        if not allowed or name in published:
            raise ValueError("INVALID_JOB_RECEIPT_PATH:" + name)
        published[name] = path
    committed = transaction.commit(published, destination_root=transaction.project_dir, expected_before=expected_before)
    return {**{name: committed["artifacts/generated/" + name] for name in files},
            **{name: committed[name] for name in (output_files or {})}}, manifest

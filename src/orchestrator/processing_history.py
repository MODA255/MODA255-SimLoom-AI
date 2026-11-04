"""Durable, bounded history for asynchronous processing observations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


def append_processing_history(result: dict, *, status: str, asset=None, progress=None, operation="asset_query") -> dict:
    """Append a sanitized processing observation without losing prior states."""
    item = {
        "at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "status": str(status).lower(),
    }
    if asset is not None:
        item["asset"] = deepcopy(asset)
    if progress is not None:
        item["progress"] = deepcopy(progress)
    result.setdefault("processing_history", []).append(item)
    return result

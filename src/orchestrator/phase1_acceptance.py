"""Hard, local acceptance gates for phase-one case runs."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .delivery_safety import strict_delivery_check

RESULT_STATUSES = {"passed", "failed", "blocked", "inconclusive", "poll_timeout"}
LIFECYCLE_STATUSES = {
    "planned", "preflight", "submitted", "running", "poll_timeout",
    "completed", "failed", "blocked", "cancelled", "cleanup_pending",
    "cleaned", "cleanup_failed",
}
TERMINAL_STATUSES = {"passed", "failed", "blocked", "inconclusive"}
CRITICAL_DELIVERY_FIELDS = (
    "delivery_enabled", "auto_delivery_enabled", "advertiser_id", "awemes",
    "products", "promotion_goal", "adlab_scene",
)


def _status(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("status")
    return value if value in LIFECYCLE_STATUSES else None


def assess_case_acceptance(*, submission: dict[str, Any] | None = None,
                           lifecycle: dict[str, Any] | None = None,
                           delivery_before: dict[str, Any] | None = None,
                           delivery_after: dict[str, Any] | None = None,
                           phase_integrity: bool | None = None,
                           business_output: bool | None = None) -> dict[str, Any]:
    submission = submission or {}
    lifecycle = lifecycle or {}
    before = delivery_before or {}
    after = delivery_after or {}
    life = _status(lifecycle)
    delivery = strict_delivery_check(before, after)
    submission_id = submission.get("task_id") or submission.get("resource_id") or submission.get("id")
    submission_invalid = submission.get("accepted") is not True or not submission_id

    if not delivery["complete"]:
        status = "inconclusive"
    elif delivery["enabled"]:
        status = "failed"
    elif submission.get("accepted") is False:
        status = "failed"
    elif life == "poll_timeout":
        status = "poll_timeout"
    elif submission_invalid:
        status = "inconclusive"
    elif life in {"failed", "cancelled", "cleanup_failed"}:
        status = "failed"
    elif life in {"blocked", "planned", "preflight", "submitted", "running", "cleanup_pending"} or submission.get("blocked"):
        status = "blocked"
    elif life is None:
        status = "inconclusive"
    elif life != "completed" or phase_integrity is not True or business_output is not True:
        status = "inconclusive"
    else:
        status = "passed"
    return {
        "status": status,
        "terminal": status in TERMINAL_STATUSES,
        "passed": status == "passed",
        "delivery": delivery,
    }


def aggregate_acceptance(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(results)
    counts = {status: sum(item.get("status") == status for item in items) for status in RESULT_STATUSES}
    if counts["failed"]:
        overall = "failed"
    elif counts["blocked"]:
        overall = "blocked"
    elif counts["inconclusive"] or counts["poll_timeout"]:
        overall = "inconclusive"
    elif items and counts["passed"] == len(items):
        overall = "passed"
    else:
        overall = "inconclusive"
    return {"status": overall, **counts, "passed": overall == "passed"}

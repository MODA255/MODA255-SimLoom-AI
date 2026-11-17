"""Versioned lifecycle state for four-software engineering operations.

The lifecycle is deliberately separate from the vendor result envelope:
``status`` describes the runner response, while this module records which
controller stage was actually reached.  A successful process exit is not
enough to mark validation as passed, and a lost connection is not treated as
cancellation.  Callers may provide an explicit phase; otherwise the helper
uses a conservative fallback that leaves uncertain stages unknown.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


SCHEMA = "simloom-ai/engineering-lifecycle/v1"
STAGES = ("preflight", "execute", "collect", "validate", "recover")
STATUSES = {"not_run", "not_requested", "running", "passed", "failed", "unknown", "deferred"}


def _entry(status: str, *, code: str | None = None, message: str | None = None) -> dict[str, str]:
    value = status if status in STATUSES else "unknown"
    result: dict[str, str] = {"status": value}
    if isinstance(code, str) and code:
        result["error_code"] = code[:160]
    if isinstance(message, str) and message:
        result["message"] = message[:512]
    return result


def empty_lifecycle() -> dict[str, Any]:
    """Return a new lifecycle object with every stage explicitly unstarted."""
    return {
        "schema_version": SCHEMA,
        "current_stage": None,
        "stages": {stage: _entry("not_run") for stage in STAGES},
        "recoverable": False,
        "next_action": None,
    }


def _stage_status(status: str, *, planned: bool = False) -> str:
    if planned:
        return "not_run"
    if status in {"completed", "committed", "passed"}:
        return "passed"
    if status in {"failed", "unavailable", "blocked", "rolled_back"}:
        return "failed"
    if status in {"waiting", "pending", "observation_pending"}:
        return "deferred"
    return "unknown"


def build_lifecycle(phase: str | None, status: str, *,
                    validation_requested: bool = False,
                    error_code: str | None = None,
                    message: str | None = None,
                    recoverable: bool = False) -> dict[str, Any]:
    """Build a lifecycle snapshot for a known phase.

    ``phase`` may be one of the five stage names or ``completed``.  Stages
    before a reached phase are marked passed only when the phase itself proves
    that they were completed.  A failed or deferred phase never marks later
    stages as successful.
    """
    result = empty_lifecycle()
    phase = phase if phase in STAGES or phase == "completed" else None
    normalized = _stage_status(status)
    if phase == "completed":
        for stage in ("preflight", "execute", "collect"):
            result["stages"][stage] = _entry("passed")
        result["stages"]["validate"] = _entry("passed" if validation_requested else "not_requested")
        result["current_stage"] = "completed"
    elif phase is not None:
        index = STAGES.index(phase)
        # Recovery is a continuation decision, not proof that the previous
        # execution stages completed.  Keep them unstarted unless the caller
        # supplies a prior lifecycle snapshot with stronger evidence.
        if phase != "recover":
            for stage in STAGES[:index]:
                result["stages"][stage] = _entry("passed")
        result["stages"][phase] = _entry(normalized, code=error_code, message=message)
        result["current_stage"] = phase
    else:
        # Conservative fallback for legacy callers that did not identify a
        # stage. Planned/unavailable responses are still attributable to
        # preflight; other failures remain unknown instead of being guessed.
        if status == "planned":
            result["stages"]["preflight"] = _entry("passed")
            result["current_stage"] = "preflight"
        elif status == "unavailable":
            result["stages"]["preflight"] = _entry("failed", code=error_code, message=message)
            result["current_stage"] = "preflight"
        elif status == "completed":
            for stage in ("preflight", "execute", "collect"):
                result["stages"][stage] = _entry("passed")
            result["stages"]["validate"] = _entry("passed" if validation_requested else "not_requested")
            result["current_stage"] = "completed"
        elif status:
            result["current_stage"] = "unknown"
            result["stages"]["execute"] = _entry("unknown", code=error_code, message=message)

    result["recoverable"] = bool(recoverable)
    if recoverable:
        result["next_action"] = "resume"
        result["stages"]["recover"] = _entry("deferred", code=error_code, message=message)
    elif result["current_stage"] in {"preflight", "execute", "collect", "validate"} and normalized == "failed":
        result["next_action"] = "review"
    return result


def normalize_lifecycle(value: Any, *, phase: str | None = None, status: str = "",
                        validation_requested: bool = False,
                        error_code: str | None = None,
                        message: str | None = None,
                        recoverable: bool = False) -> dict[str, Any]:
    """Validate a supplied lifecycle or create a bounded fallback snapshot."""
    if isinstance(value, dict) and value.get("schema_version") == SCHEMA:
        result = deepcopy(value)
        stages = result.get("stages")
        if not isinstance(stages, dict):
            stages = {}
        result["stages"] = {
            stage: _entry((stages.get(stage) or {}).get("status", "unknown"),
                          code=(stages.get(stage) or {}).get("error_code"),
                          message=(stages.get(stage) or {}).get("message"))
            for stage in STAGES
        }
        result["current_stage"] = result.get("current_stage") if result.get("current_stage") in set(STAGES) | {"completed", "unknown", None} else "unknown"
        result["recoverable"] = bool(result.get("recoverable", False))
        result["next_action"] = result.get("next_action") if isinstance(result.get("next_action"), str) else None
        return result
    return build_lifecycle(phase, status, validation_requested=validation_requested,
                           error_code=error_code, message=message, recoverable=recoverable)


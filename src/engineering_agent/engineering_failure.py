"""Conservative classification of four-software execution failures.

Error codes remain the stable low-level API.  This module adds a small
agent-facing interpretation so a planner can choose between fixing inputs,
resuming an existing remote job, retrying a transport request, or asking an
engineer to inspect a licensed application.  Unknown codes are deliberately
classified as ``unknown`` and require review.
"""
from __future__ import annotations

from typing import Any


SCHEMA = "simloom-ai/engineering-failure/v1"
CATEGORIES = {
    "configuration", "input_contract", "transport", "execution", "collection",
    "validation", "recovery", "concurrency", "publication", "unknown",
}


def _classification(category: str, *, retryable: bool, requires_human: bool,
                    action: str, code: str | None, message: str | None,
                    confidence: str = "high") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "category": category if category in CATEGORIES else "unknown",
        "retryable": bool(retryable),
        "requires_human": bool(requires_human),
        "recommended_action": action,
        "confidence": confidence,
        "error_code": code,
        **({"message": message[:512]} if isinstance(message, str) and message else {}),
    }


def classify_failure(code: str | None, *, status: str = "failed",
                    lifecycle: dict[str, Any] | None = None,
                    message: str | None = None) -> dict[str, Any]:
    """Classify a low-level error without claiming vendor semantics."""
    code = code if isinstance(code, str) and code else None
    current = lifecycle.get("current_stage") if isinstance(lifecycle, dict) else None
    recoverable = bool(lifecycle and lifecycle.get("recoverable"))
    if status in {"planned", "completed"} and not code:
        return _classification("unknown", retryable=False, requires_human=False,
                                action="none", code=None, message=message,
                                confidence="medium")

    configuration = {"ACTION_BINDING_REQUIRED", "COMMAND_NOT_CONFIGURED", "COMMAND_SCRIPT_BINDING_REQUIRED",
                     "BRIDGE_TOKEN_MISSING", "REMOTE_WORKER_NOT_CONFIGURED", "VENDOR_PROG_ID_REQUIRED",
                     "CATIA_PROG_ID_REQUIRED", "VENDOR_COMMAND_REQUIRED", "COMMAND_MUST_BE_CONFIGURED",
                     "VENDOR_CONTRACT_UNSUPPORTED", "INVALID_VALIDATION_CONTRACT", "OUTPUT_CONTRACT_REQUIRED"}
    input_contract = {"INVALID_PARAMETERS", "INVALID_SOFTWARE_ACTION", "INVALID_FILE_LIST", "INPUT_MANIFEST_MISMATCH",
                      "ARTIFACT_MISSING", "ARTIFACT_HASH_MISMATCH", "VENDOR_INPUT_CHANGED", "VENDOR_CONTRACT_INVALID",
                      "VENDOR_FILE_CONTRACT_MISMATCH", "WORKFLOW_INPUT_MISSING", "WORKFLOW_INPUT_CHANGED",
                      "PROJECT_OUTPUT_CHANGED", "PATH_OUTSIDE_PROJECT", "INVALID_ARTIFACT_PATH"}
    transport = {"BRIDGE_HTTP_ERROR", "BRIDGE_HEALTH_FAILED", "WORKER_SCHEMA_UNSUPPORTED", "WORKER_RESPONSE_MISMATCH",
                 "TRANSFER_TOO_LARGE", "TRANSFER_CHUNK_HASH_MISMATCH", "TRANSFER_FILE_HASH_MISMATCH",
                 "TRANSFER_RESPONSE_MISMATCH", "TRANSFER_SIZE_MISMATCH", "OUTPUT_TRANSFER_REQUIRES_BRIDGE",
                 "REMOTE_JOB_PENDING"}
    execution = {"SOFTWARE_START_FAILED", "SOFTWARE_EXIT_FAILED", "SOFTWARE_TIMEOUT", "JOB_CANCELLED",
                 "VENDOR_PROCESS_FAILED", "INVALID_WORKER_COMPLETION", "JOB_NOT_FOUND"}
    collection = {"OUTPUT_MANIFEST_MISMATCH", "OUTPUT_EMPTY", "OUTPUT_MISSING", "ARTIFACT_EMPTY",
                  "VENDOR_REPORT_MISSING", "VENDOR_OUTPUT_MISSING", "INVALID_ARTIFACT_ENCODING"}
    validation = {"VENDOR_REPORT_INVALID", "VENDOR_REPORT_FAILED", "VENDOR_CHECK_FAILED", "VENDOR_CHECK_MISSING",
                  "VENDOR_CHECK_INVALID", "VENDOR_ARTIFACT_MISMATCH", "VENDOR_REPORT_MISMATCH", "INVALID_METRICS_EXPORT",
                  "METRIC_MISSING", "INCOMPATIBLE_UNITS", "MECHANICAL_SOLUTION_INCOMPLETE", "MECHANICAL_RESULT_UNSOLVED",
                  "NATIVE_REOPEN_REQUIRED", "PARAMETER_READBACK_MISMATCH", "STEP_EXPORT_INVALID"}
    concurrency = {"PROJECT_LOCKED", "LOCAL_JOB_RECEIPT_CONFLICT", "JOB_CONNECTION_CHANGED", "PUBLISHED_RESULT_CHANGED",
                   "WORKFLOW_BINDING_CHANGED", "WORKFLOW_PLAN_CHANGED", "WORKFLOW_REQUEST_CHANGED"}
    publication = {"PUBLICATION_FAILED", "CAD_PERSISTED_VALIDATION_FAILED", "BREP_PERSISTED_VALIDATION_FAILED",
                   "VIEW_EXPORT_PERSISTED_INVALID"}
    recovery = {"REMOTE_JOB_HISTORY_LOST", "LOCAL_STEP_STATE_UNKNOWN", "WORKFLOW_COLLECTION_REQUIRED", "JOB_NOT_COMPLETED"}

    if code in recovery or current == "recover":
        return _classification("recovery", retryable=code in {"JOB_NOT_COMPLETED", "REMOTE_JOB_PENDING"} or recoverable,
                                requires_human=code in {"REMOTE_JOB_HISTORY_LOST", "LOCAL_STEP_STATE_UNKNOWN"},
                                action="resume" if recoverable or code == "JOB_NOT_COMPLETED" else "inspect_remote_job",
                                code=code, message=message)
    if code in configuration:
        return _classification("configuration", retryable=False, requires_human=True,
                                action="fix_configuration", code=code, message=message)
    if code in input_contract:
        return _classification("input_contract", retryable=False, requires_human=False,
                                action="fix_inputs_or_contract", code=code, message=message)
    if code in transport:
        return _classification("transport", retryable=True, requires_human=False,
                                action="retry_or_resume", code=code, message=message)
    if code in execution:
        return _classification("execution", retryable=False, requires_human=code in {"SOFTWARE_TIMEOUT", "JOB_CANCELLED"},
                                action="inspect_software_session" if code in {"SOFTWARE_TIMEOUT", "JOB_CANCELLED"} else "review_runner",
                                code=code, message=message)
    if code in collection:
        return _classification("collection", retryable=True, requires_human=False,
                                action="recollect_or_inspect_outputs", code=code, message=message)
    if code in validation:
        return _classification("validation", retryable=False, requires_human=False,
                                action="review_engineering_result", code=code, message=message)
    if code in concurrency:
        return _classification("concurrency", retryable=True, requires_human=False,
                                action="refresh_state_and_retry", code=code, message=message)
    if code in publication:
        return _classification("publication", retryable=True, requires_human=False,
                                action="restore_transaction_and_retry_publication", code=code, message=message)
    return _classification("unknown", retryable=False, requires_human=True,
                           action="manual_review", code=code, message=message, confidence="low")


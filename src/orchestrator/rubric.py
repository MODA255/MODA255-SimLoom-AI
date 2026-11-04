from urllib.parse import urlparse

RUBRIC_NAMES = (
    "input_validity",
    "routing_correctness",
    "parameter_correctness",
    "phase_completeness",
    "output_completeness",
    "remote_metadata",
    "delivery_safety",
)


def _check(name, passed, evidence, failure_mode=None):
    item = {"status": "passed" if passed else "failed", "evidence": evidence}
    if failure_mode and not passed:
        item["failure_mode"] = failure_mode
    return item


def _remote_url_valid(video):
    parsed = urlparse(str(video.get("oss_url") or video.get("remote_url") or ""))
    return parsed.scheme == "https" and bool(parsed.netloc)


def evaluate_chain(data):
    data = data or {}
    task = data.get("task") or {}
    parent = data.get("parent_task") or {}
    videos = data.get("videos") or []
    failures = []
    rubric = {}

    input_valid = bool(data.get("input_valid", True))
    rubric["input_validity"] = _check("input_validity", input_valid, "input_valid=true" if input_valid else "input_valid=false", "INPUT_INVALID")
    if not input_valid: failures.append("INPUT_INVALID")

    expected_recipe = data.get("expected_recipe")
    routing_ok = not expected_recipe or task.get("recipe") == expected_recipe
    rubric["routing_correctness"] = _check("routing_correctness", routing_ok, f"actual={task.get('recipe')}, expected={expected_recipe}", "SCENE_ROUTING_MISMATCH")
    if not routing_ok: failures.append("SCENE_ROUTING_MISMATCH")

    delivery_known = "delivery_enabled" in task and "auto_delivery_enabled" in task
    delivery_ok = delivery_known and task.get("delivery_enabled") is False and task.get("auto_delivery_enabled") is False
    phases_ok = task.get("completed_phases") == task.get("total_phases") and task.get("status") == "completed"
    rubric["parameter_correctness"] = _check("parameter_correctness", True, "task parameters accepted")
    rubric["phase_completeness"] = _check("phase_completeness", phases_ok, f"status={task.get('status')}, phases={task.get('completed_phases')}/{task.get('total_phases')}", "PHASE_INCOMPLETE")
    if not phases_ok: failures.append("PHASE_INCOMPLETE")

    expected_count = int(data.get("expected_video_count") or 0)
    outputs_ok = len(videos) >= expected_count and expected_count >= 0
    rubric["output_completeness"] = _check("output_completeness", outputs_ok, f"actual={len(videos)}, expected={expected_count}", "OUTPUT_MISSING")
    if not outputs_ok: failures.append("OUTPUT_MISSING")

    metadata_ok = bool(videos) and all(v.get("id") and v.get("status") == "created" and _remote_url_valid(v) and v.get("duration") is not None and v.get("resolution") and v.get("file_size") is not None for v in videos)
    rubric["remote_metadata"] = _check("remote_metadata", metadata_ok, f"validated={len(videos)} remote videos", "REMOTE_METADATA_INVALID")
    if not metadata_ok: failures.append("REMOTE_METADATA_INVALID")

    rubric["delivery_safety"] = _check("delivery_safety", delivery_ok, f"delivery_enabled={task.get('delivery_enabled')}, auto_delivery_enabled={task.get('auto_delivery_enabled')}", "DELIVERY_POLICY_VIOLATION" if delivery_known else "DELIVERY_POLICY_UNKNOWN")
    if not delivery_known:
        failures.append("DELIVERY_POLICY_UNKNOWN")
    elif not delivery_ok:
        failures.append("DELIVERY_POLICY_VIOLATION")

    if parent.get("status") == "waiting_live":
        failures.append("PARENT_WAITING_LIVE")
    if data.get("promotion_status") == "blocked" and data.get("promotion_block_reason") == "delivery_disabled":
        failures.append("PROMOTION_BLOCKED_BY_POLICY")

    hard_failures = [x for x in failures if x not in {"PARENT_WAITING_LIVE", "PROMOTION_BLOCKED_BY_POLICY", "DELIVERY_POLICY_UNKNOWN"}]
    if hard_failures:
        overall = "failed"
    elif failures:
        overall = "passed_with_warning"
    else:
        overall = "passed"
    return {
        "overall": overall,
        "passed": not hard_failures,
        "rubric": rubric,
        "failure_modes": failures,
        "evidence": [item["evidence"] for item in rubric.values()],
    }

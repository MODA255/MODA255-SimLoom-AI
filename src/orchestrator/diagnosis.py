def diagnose_chain(data):
    data = data or {}
    completeness = data.get("evidence_completeness") or {}
    cleanup = data.get("cleanup") or {"status": "not_started"}
    if completeness.get("status") in {"insufficient", "unavailable"}:
        return {"status":"insufficient_evidence", "cannot_conclude":True, "failure_stage":None, "failure_mode":None, "root_cause":None, "next_action":"COLLECT_MISSING_EVIDENCE", "missing_evidence":list(completeness.get("missing") or []), "cleanup_status":cleanup.get("status", "not_started")}
    phase_rubric = data.get("phase_rubric") or {}
    phases = phase_rubric.get("phases") or {}
    failure_stage = next((name for name, item in phases.items() if item.get("status") == "failed"), None)
    failures = []
    if failure_stage:
        failures = list(phases[failure_stage].get("failure_modes") or [])
    root_cause = None
    next_action = "REVIEW_TASK_EVIDENCE"
    if failure_stage in {"select", "segment", "refine_slicer"}:
        root_cause = "selection_or_material_pipeline"
        next_action = "检查素材标签、切片池和选片输出"
    elif failure_stage in {"arrange", "compose"}:
        root_cause = "timeline_or_compose_pipeline"
        next_action = "检查时间轴、片段血缘和合成输入"
    elif failure_stage in {"subtitle", "transcript"}:
        root_cause = "text_artifact_pipeline"
        next_action = "检查 ASR、字幕或脚本产物"
    elif failure_stage == "upload":
        root_cause = "output_persistence_pipeline"
        next_action = "检查远程对象存储和成片回写"
    elif failures:
        root_cause = "workflow_phase_execution"
        next_action = "检查阶段日志和 workflow_run_id"
    status = "diagnosed" if root_cause else "no_failure"
    return {"status":status, "cannot_conclude":False, "failure_stage":failure_stage, "failure_mode":failures[0] if failures else None, "root_cause":root_cause, "next_action":next_action, "missing_evidence":[], "cleanup_status":cleanup.get("status", "not_started"), "cleanup_failures":cleanup.get("cleanup_failures", [])}

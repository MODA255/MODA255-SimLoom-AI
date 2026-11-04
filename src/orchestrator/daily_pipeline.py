import json
from pathlib import Path
from datetime import datetime, timezone
from .chain_report import build_six_chain_report, write_structured_report
from .llm_judge import LLMJudge, evaluate_judge_evidence
from .evidence_adapter import merge_phase_node_evidence, aggregate_text_and_lineage, collect_diagnostic_summaries
from .judge_evidence import build_judge_evidence
from .phase_rubric import evaluate_phases
from .runner import poll_task
from .browser_observer import capture_task_screenshot


def _video_items(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.get("items") or value.get("videos") or []
    return []


def collect_chain_evidence(adapter, spec):
    task_id = spec["task_id"]
    evidence = dict(spec)
    fallback_error = None
    poll_data = {"poll_events": [], "poll_status": "not_run"}
    if spec.get("poll", True) and hasattr(adapter, "get_task"):
        try:
            poll_data = poll_task(adapter, task_id, timeout_seconds=spec.get("timeout_seconds", 3600), interval_seconds=spec.get("poll_interval_seconds", 20))
            evidence.update(poll_data)
        except Exception as exc:
            poll_data = {"poll_events": [], "poll_status": "error", "poll_error": f"{type(exc).__name__}: {exc}"}
            evidence.update(poll_data)
    if hasattr(adapter, "get_evidence"):
        try:
            route_evidence = dict(adapter.get_evidence(task_id) or {})
            route_evidence.update({key: value for key, value in evidence.items() if key not in route_evidence})
            evidence = route_evidence
        except Exception as exc:
            fallback_error = {"type": type(exc).__name__, "message": str(exc)}
    task = evidence.get("task") or adapter.get_task(task_id)
    nodes = None
    if hasattr(adapter, "get_nodes"):
        try: nodes = adapter.get_nodes(task_id)
        except Exception: nodes = None
    phases = []
    if hasattr(adapter, "get_phases"):
        try: phases = _video_items(adapter.get_phases(task_id))
        except Exception: phases = []
    videos = _video_items(adapter.get_videos(task_id))
    diagnostics = {}
    if hasattr(adapter, "get_diagnostics"):
        try: diagnostics = adapter.get_diagnostics(task_id, ["pipeline.log", "viral_learning_plans.json", "auto_theme_audit.json", "refine_slicer_report.json", "timeline_review_report.json", "arrange_report.json"])
        except Exception: diagnostics = {}
    enriched = []
    for video in videos:
        video = dict(video)
        if not video.get("transcript_json") and hasattr(adapter, "get_transcript"):
            transcript = adapter.get_transcript(task_id, video.get("id") or video.get("video_id"))
            if transcript is not None: video["transcript_json"] = transcript
        enriched.append(video)
    evidence.update({"task": task, "videos": enriched, "nodes": (nodes or {}).get("items", []) if isinstance(nodes, dict) else nodes, "phases": phases or task.get("phases") or evidence.get("phases") or [], "poll_events": evidence.get("poll_events", []), "screenshot_status": evidence.get("screenshot_status", "not_collected"), "diagnostics": collect_diagnostic_summaries(diagnostics), "text_artifacts": [], "lineage": [], "evidence_source": "legacy_nodes_api" if nodes is not None else "legacy_task_api", "local_media_files": [], "input_valid": spec.get("input_valid", True)})
    screenshot = None
    if spec.get("capture_screenshot"):
        screenshot = capture_task_screenshot(spec.get("frontend_origin", ""), task_id, spec.get("screenshot_dir", "runs/screenshots"), storage_state_path=spec.get("storage_state_path"), route_template=spec.get("screenshot_route", "/create/vfc-library?task_id={task_id}"))
        evidence["screenshot_status"] = screenshot.get("status")
        evidence["screenshots"] = [screenshot["path"]] if screenshot.get("path") else []
        if screenshot.get("error"):
            evidence.setdefault("evidence_errors", []).append(screenshot["error"])
    evidence.update(aggregate_text_and_lineage(task, enriched))
    evidence["evidence_collection"] = {"status":"completed" if phases and nodes and (evidence.get("text_artifacts") or evidence.get("lineage")) else "partial", "sources_checked":["task","poll_events","phases","nodes","diagnostics","videos","transcript","script","lineage"], "missing":[] if phases and nodes and evidence.get("text_artifacts") and evidence.get("lineage") else ["transcript_or_script"], "errors":evidence.get("evidence_errors", [])}
    if fallback_error: evidence["evidence_api_error"] = fallback_error
    return evidence


def _judge_payload(evidence):
    task = evidence.get("task") or {}
    return {
        "task_id": task.get("id"),
        "scene_type": evidence.get("scene_type"),
        "recipe": task.get("recipe"),
        "query": task.get("query"),
        "brief": task.get("brief"),
        "brand_name": task.get("brand_name"),
        "ip_name": task.get("ip_name"),
        "category": task.get("category"),
        "delivery_enabled": task.get("delivery_enabled"),
        "auto_delivery_enabled": task.get("auto_delivery_enabled"),
        "phase_names": task.get("phase_names"),
        "status": task.get("status"),
        "videos": evidence.get("videos", []),
    }


def _judge_result(result):
    return {
        "overall_score": result.overall_score,
        "confidence": result.confidence,
        "reason": result.reason,
        "failure_modes": result.failure_modes,
        "evidence": result.evidence,
        "model": result.model,
        "prompt_version": result.prompt_version,
        "input_sha256": result.input_sha256,
        "scores": result.scores,
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _attach_timing(evidence):
    events = evidence.get('poll_events') or []
    task = evidence.get('task') or {}
    if events:
        evidence['poll_events'] = events
        evidence.setdefault('started_at', events[0].get('observed_at'))
        evidence.setdefault('finished_at', events[-1].get('observed_at'))
    evidence.setdefault('evidence_collected_at', _utc_now())
    evidence.setdefault('screenshot_status', 'not_collected')
    return evidence


def run_daily_pipeline(adapter, specs, output_dir, judge=None):
    judge = judge or LLMJudge()
    inputs = []
    for spec in specs:
        evidence = collect_chain_evidence(adapter, spec)
        evidence = _attach_timing(evidence)
        try:
            evidence["phase_rubric"] = evaluate_phases(evidence.get("phases", []), evidence.get("expected_video_count", 1))
            evidence["phases"] = merge_phase_node_evidence(evidence.get("phases", []), evidence.get("nodes", []))
            evidence.update(aggregate_text_and_lineage(evidence.get("task", {}), evidence.get("videos", [])))
            evidence["diagnostics"] = evidence.get("diagnostics", [])
            evidence["nodes"] = evidence.get("nodes", [])
            evidence["evidence_completeness"] = evidence.get("evidence_completeness", {"status":"insufficient", "missing": ["diagnostics", "transcript_or_script", "lineage"]})
            evidence["judge_evidence"] = build_judge_evidence({"task": evidence.get("task"), "nodes": evidence.get("nodes"), "phases": evidence.get("phases"), "diagnostics": evidence.get("diagnostics"), "text_artifacts": evidence.get("text_artifacts"), "lineage": evidence.get("lineage"), "deterministic_findings": evidence.get("phase_rubric")})
            gate = evaluate_judge_evidence(evidence.get("judge_evidence", {}))
            if gate["status"] != "ready":
                evidence["judge"] = gate
            else:
                evidence["judge"] = _judge_result(judge.evaluate(evidence["judge_evidence"]))
        except Exception as exc:
            evidence["judge"] = {"status": "error", "error_code": type(exc).__name__, "error": str(exc), "failure_modes": ["JUDGE_EXECUTION_FAILED"], "debug": repr(exc)}
        inputs.append(evidence)
    report = build_six_chain_report(inputs)
    report["report_files"] = write_structured_report(report, output_dir)
    return report

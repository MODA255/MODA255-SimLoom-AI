import json
from .chain_report import build_six_chain_report


def _chain_input(chain):
    videos = chain.get("videos") or {}
    items = videos.get("items", []) if isinstance(videos, dict) else videos
    task = chain.get("task") or {}
    return {
        "chain_id": chain.get("chain_id"),
        "execution_mode": chain.get("execution_mode"),
        "scene_type": chain.get("scene_type"),
        "expected_recipe": task.get("recipe") or chain.get("scene_type"),
        "task": {
            "id": task.get("task_id"),
            "status": task.get("status"),
            "recipe": task.get("recipe"),
            "delivery_enabled": chain.get("delivery_status", {}).get("status") == "disabled" if isinstance(chain.get("delivery_status"), dict) else None,
            "auto_delivery_enabled": False,
            "completed_phases": task.get("completed_phases"),
            "total_phases": task.get("total_phases"),
        },
        "parent_task": {"id": task.get("parent_task_id"), "status": task.get("parent_status")},
        "videos": items,
        "expected_video_count": videos.get("requested", len(items)) if isinstance(videos, dict) else len(items),
        "input_valid": True,
        "phase_rubric": chain.get("phase_rubric"),
        "judge": chain.get("llm_judge"),
        "cleanup": {"status": chain.get("cleanup_status", "not_started")},
        "evidence_completeness": chain.get("evidence_completeness", {"status": "insufficient", "missing": ["replay_input_normalization"]}),
    }


def replay_report(report):
    inputs = [_chain_input(chain) for chain in report.get("chains") or []]
    result = build_six_chain_report(inputs)
    result["mode"] = "offline_replay"
    result["remote_writes"] = False
    return result

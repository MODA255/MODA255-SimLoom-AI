import hashlib
import json


def normalize_phase(phase, task_id=None):
    phase = dict(phase or {})
    output_files = phase.get("output_files") or []
    output_data = phase.get("output_data") or {}
    return {
        "task_id": task_id,
        "phase_id": phase.get("id") or phase.get("phase_id"),
        "phase_name": phase.get("phase_name") or phase.get("name") or phase.get("phase"),
        "phase_index": phase.get("phase_index"),
        "status": phase.get("status"),
        "message": phase.get("message"),
        "error_msg": phase.get("error_msg"),
        "started_at": phase.get("started_at"),
        "completed_at": phase.get("completed_at"),
        "duration_ms": phase.get("duration_ms"),
        "retry_count": phase.get("retry_count", 0),
        "metrics": phase.get("metrics") or {},
        "output_data": output_data,
        "output_files": output_files,
        "previews": phase.get("phase_previews") or phase.get("previews") or [],
        "workflow_run_id": output_data.get("workflow_run_id") if isinstance(output_data, dict) else None,
        "trace_id": phase.get("trace_id"),
        "evidence_status": "available" if phase else "missing",
    }


def _artifact(artifact_type, phase_name, content=None, remote_url=None, evidence_status=None):
    if isinstance(content, dict):
        text = content.get("text") or content.get("raw_text") or json.dumps(content, ensure_ascii=False)
    else:
        text = str(content) if content is not None else ""
    status = evidence_status or ("available" if text else "missing")
    return {
        "artifact_id": None,
        "artifact_type": artifact_type,
        "phase_name": phase_name,
        "remote_url": remote_url,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "text_length": len(text),
        "language": "zh-CN",
        "content": text if text else None,
        "evidence_status": status,
    }


def collect_text_artifacts(video=None, task=None):
    video = video or {}; task = task or {}
    artifacts = []
    transcript = video.get("transcript_json")
    if transcript:
        artifacts.append(_artifact("transcript", "transcript", transcript, video.get("transcript_oss_url")))
    elif video.get("transcript_oss_url"):
        artifacts.append(_artifact("transcript", "transcript", remote_url=video["transcript_oss_url"], evidence_status="available_remote"))
    elif task.get("transcript_json"):
        artifacts.append(_artifact("transcript", "transcript", task["transcript_json"]))
    script = task.get("script") or task.get("user_script") or task.get("scripts_text")
    if script:
        artifacts.append(_artifact("script", "script", script))
    if not artifacts:
        artifacts.append(_artifact("transcript", "transcript"))
        artifacts.append(_artifact("script", "script"))
    return artifacts


def build_lineage(video_id, clips, asset_id=None, transformations=None):
    records = []
    for clip in clips or []:
        records.append({
            "final_video_id": video_id,
            "final_clip_id": clip.get("clip_id") or clip.get("id"),
            "source_clip_id": clip.get("asset_clip_id") or clip.get("source_clip_id") or clip.get("parent_clip_id"),
            "asset_id": asset_id,
            "source_start": clip.get("source_start"),
            "source_end": clip.get("source_end"),
            "final_start": clip.get("start_time"),
            "final_end": clip.get("end_time"),
            "transformations": list(transformations or []),
        })
    return records

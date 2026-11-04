import json
from pathlib import Path

from orchestrator.daily_pipeline import collect_chain_evidence, run_daily_pipeline


class Adapter:
    def __init__(self):
        self.calls = []

    def get_task(self, task_id):
        self.calls.append(("task", task_id))
        return {"id": task_id, "status": "completed", "recipe": "auto_theme_discovery", "delivery_enabled": False, "auto_delivery_enabled": False, "completed_phases": 2, "total_phases": 2, "query": "主题", "brief": "根据证据", "brand_name": "朵薇", "category": "个护"}

    def get_videos(self, task_id):
        self.calls.append(("videos", task_id))
        return [{"id": "video-1", "status": "created", "oss_url": "https://storage/video.mp4", "duration": 10, "resolution": "1080x1920", "file_size": 100, "transcript_oss_url": "https://storage/transcript.json", "transcript_json": {"text": "朵薇卫生巾主题内容"}, "clip_ids": [{"clip_id": "clip-1", "asset_clip_id": "source-1", "source_start": 0, "source_end": 1, "start_time": 0, "end_time": 1}]}]

    def get_transcript(self, task_id, video_id):
        self.calls.append(("transcript", task_id, video_id))
        return {"text": "朵薇卫生巾主题内容"}


def test_collect_chain_evidence_fetches_task_videos_and_transcript_without_download():
    evidence = collect_chain_evidence(Adapter(), {"chain_id": "manual_theme", "task_id": "task-1", "expected_video_count": 1, "expected_recipe": "auto_theme_discovery"})
    assert evidence["task"]["id"] == "task-1"
    assert evidence["videos"][0]["transcript_json"]["text"] == "朵薇卫生巾主题内容"
    assert evidence["local_media_files"] == []


def test_collect_chain_evidence_defaults_to_explicit_missing_evidence_states():
    evidence = collect_chain_evidence(Adapter(), {"chain_id": "manual_theme", "task_id": "task-1"})
    assert len(evidence["poll_events"]) == 1
    assert evidence["poll_events"][0]["status"] == "completed"
    assert evidence["screenshot_status"] == "not_collected"


def test_run_daily_pipeline_collects_rubric_judge_and_writes_report(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setattr("orchestrator.daily_pipeline.LLMJudge.evaluate", lambda self, payload: type("R", (), {"overall_score": 90, "confidence": 0.9, "reason": "ok", "failure_modes": [], "evidence": ["quote"], "model": "judge", "prompt_version": "v1", "input_sha256": "hash", "scores": {"topic_fit": 5}})())
    result = run_daily_pipeline(adapter, [{"chain_id": "manual_theme", "task_id": "task-1", "expected_video_count": 1, "expected_recipe": "auto_theme_discovery", "execution_mode": "manual", "scene_type": "auto_theme_discovery"}], tmp_path)
    assert result["chain_count"] == 1
    assert result["chains"][0]["llm_judge"]["overall_score"] in {None, 90}
    assert result["chains"][0]["llm_judge"]["status"] in {"completed", "insufficient_evidence", "error"}
    assert result["chains"][0]["phase_rubric"]["status"] in {"passed", "warning"}
    assert Path(result["report_files"]["json"]).exists()
    assert not list(tmp_path.glob("*.mp4"))

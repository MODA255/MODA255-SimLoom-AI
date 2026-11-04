import json
from pathlib import Path

from orchestrator.chain_report import build_chain_report, build_six_chain_report, write_structured_report


def task(recipe, status="completed"):
    return {
        "id": "task-1", "recipe": recipe, "status": status,
        "delivery_enabled": False, "auto_delivery_enabled": False,
        "completed_phases": 2, "total_phases": 2,
        "query": "朵薇卫生巾主题", "brief": "仅基于证据生成内容",
        "brand_name": "朵薇", "category": "个护家清",
    }


def video():
    return {"id": "video-1", "status": "created", "oss_url": "https://storage.example/video.mp4", "duration": 30, "resolution": "1080x1920", "file_size": 100, "transcript_json": {"text": "朵薇卫生巾"}}


def test_build_chain_report_unifies_rubric_judge_remote_metadata_and_transcript():
    result = build_chain_report({
        "chain_id": "manual_theme", "execution_mode": "manual", "scene_type": "auto_theme_discovery",
        "expected_recipe": "auto_theme_discovery", "task": task("auto_theme_discovery"),
        "videos": [video()], "expected_video_count": 1, "input_valid": True,
        "judge": {"overall_score": 88, "confidence": 0.9, "failure_modes": [], "reason": "符合主题"},
    })

    assert result["chain_id"] == "manual_theme"
    assert result["deterministic_rubric"]["overall"] == "passed"
    assert result["llm_judge"]["overall_score"] == 88
    assert result["videos"]["items"][0]["local_file"] is None
    assert result["videos"]["items"][0]["remote_url"] == "https://storage.example/video.mp4"
    assert result["videos"]["items"][0]["transcript"]["text"] == "朵薇卫生巾"
    assert result["final_status"] == "passed"


def test_six_chain_report_keeps_waiting_parent_and_policy_warning_visible():
    output = build_six_chain_report([
        {"chain_id": "auto_ecom", "execution_mode": "automation", "scene_type": "ecom_mix", "expected_recipe": "ecom_remix_viral_learning", "parent_task": {"id": "parent", "status": "waiting_live"}, "task": task("ecom_remix_viral_learning"), "videos": [video()], "expected_video_count": 1, "promotion_status": "blocked", "promotion_block_reason": "delivery_disabled"},
        {"chain_id": "manual_theme", "execution_mode": "manual", "scene_type": "auto_theme_discovery", "expected_recipe": "auto_theme_discovery", "task": task("auto_theme_discovery"), "videos": [video()], "expected_video_count": 1},
    ])

    assert output["chain_count"] == 2
    auto = next(x for x in output["chains"] if x["chain_id"] == "auto_ecom")
    assert auto["final_status"] == "passed_with_warning"
    assert "PARENT_WAITING_LIVE" in auto["failure_modes"]
    assert "PROMOTION_BLOCKED_BY_POLICY" in auto["failure_modes"]


def test_write_structured_report_writes_json_and_markdown_without_media_files(tmp_path):
    report = build_six_chain_report([])
    paths = write_structured_report(report, tmp_path)
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert not list(tmp_path.glob("*.mp4"))
    json.loads(Path(paths["json"]).read_text())

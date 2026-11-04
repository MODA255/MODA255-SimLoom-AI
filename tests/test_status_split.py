from orchestrator.phase_rubric import evaluate_phases
from orchestrator.chain_report import build_chain_report


def test_phase_rubric_covers_pipeline_phases_and_skips_are_normal():
    result = evaluate_phases([
        {"phase_name":"material_download","status":"completed","metrics":{"bytes_done":100,"bytes_total":100}},
        {"phase_name":"segment","status":"completed","metrics":{"total_clips":10,"unlabeled_ratio":0}},
        {"phase_name":"viral_learning","status":"completed","metrics":{"requested":1,"final":1,"shortfall":False}},
        {"phase_name":"subtitle","status":"skipped","message":"未开启字幕"},
        {"phase_name":"bgm","status":"skipped","message":"未开启 BGM"},
        {"phase_name":"transcript","status":"completed","output_data":{"text_length":100}},
    ], expected_video_count=1)
    assert result["phases"]["material_download"]["status"] == "passed"
    assert result["phases"]["segment"]["status"] == "passed"
    assert result["phases"]["viral_learning"]["status"] == "passed"
    assert result["phases"]["subtitle"]["status"] == "skipped"
    assert result["phases"]["bgm"]["status"] == "skipped"
    assert result["phases"]["transcript"]["status"] == "passed"


def test_chain_report_splits_execution_content_delivery_and_cleanup_status():
    result = build_chain_report({
        "chain_id":"auto_ecom", "execution_mode":"automation", "scene_type":"ecom_mix",
        "expected_recipe":"ecom_remix_viral_learning", "parent_task":{"id":"p1","status":"waiting_live"},
        "task":{"id":"c1","status":"completed","recipe":"ecom_remix_viral_learning","delivery_enabled":False,"auto_delivery_enabled":False,"completed_phases":2,"total_phases":2},
        "videos":[{"id":"v1","status":"created","oss_url":"https://oss/v.mp4","duration":10,"resolution":"1080x1920","file_size":100,"transcript_json":{"text":"内容"},"clip_ids":[{"clip_id":"c","asset_clip_id":"s","source_start":0,"source_end":1,"start_time":0,"end_time":1}]}],
        "expected_video_count":1, "phase_rubric":{"status":"passed"},
        "judge":{"status":"insufficient_evidence","overall_score":None,"confidence":0,"failure_modes":["MISSING_JUDGE_EVIDENCE"]},
        "cleanup":{"status":"not_started"}, "promotion_status":"blocked", "promotion_block_reason":"delivery_disabled",
    })
    assert result["execution_status"] == "passed"
    assert result["content_evaluation_status"] == "insufficient_evidence"
    assert result["delivery_status"]["status"] == "disabled"
    assert result["parent_task_status"] == "waiting_live"
    assert result["cleanup_status"] == "not_started"
    assert result["final_status"] == "passed_with_warning"

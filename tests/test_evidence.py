from orchestrator.evidence import normalize_phase, collect_text_artifacts, build_lineage


def test_normalize_phase_preserves_available_fields_and_marks_missing_refs():
    out = normalize_phase({"id":"p1","phase_name":"select","status":"completed","metrics":{"selected_count":5},"output_data":{"shortfall":False}} , task_id="t1")
    assert out["phase_id"] == "p1"
    assert out["phase_name"] == "select"
    assert out["evidence_status"] == "available"
    assert out["output_files"] == []
    assert out["task_id"] == "t1"


def test_collect_text_artifacts_prioritizes_transcript_and_marks_missing():
    artifacts = collect_text_artifacts({"id":"v1","transcript_json":{"text":"字幕"},"transcript_oss_url":"https://x"}, {"script":"脚本"})
    assert artifacts[0]["artifact_type"] == "transcript"
    assert artifacts[0]["text_length"] == 2
    assert artifacts[0]["evidence_status"] == "available"
    missing = collect_text_artifacts({"id":"v2"}, {})
    assert any(x["evidence_status"] == "missing" for x in missing)


def test_build_lineage_uses_clip_source_ranges_without_media_download():
    records = build_lineage("video-1", [{"clip_id":"c1","asset_clip_id":"source-1","source_start":1.2,"source_end":4.5,"start_time":0,"end_time":3.3}], "asset-1")
    assert records[0]["final_video_id"] == "video-1"
    assert records[0]["source_clip_id"] == "source-1"
    assert records[0]["asset_id"] == "asset-1"
    assert records[0]["transformations"] == []

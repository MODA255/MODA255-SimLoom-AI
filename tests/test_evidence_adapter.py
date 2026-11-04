from orchestrator.evidence_adapter import merge_phase_node_evidence, collect_diagnostic_summaries, aggregate_text_and_lineage


def test_merge_phase_node_evidence_keeps_node_io_and_phase_metrics():
    result = merge_phase_node_evidence(
        [{"phase_name":"select","status":"completed","metrics":{"selected_count":3},"output_data":{"workflow_run_id":"r1"}}],
        [{"key":"select","status":"completed","input":{"clips":10},"output":{"selected":3},"calls":[{"model":"m"}]}],
    )
    assert result[0]["key"] == "select"
    assert result[0]["metrics"]["selected_count"] == 3
    assert result[0]["input"] == {"clips":10}
    assert result[0]["output"] == {"selected":3}


def test_collect_diagnostic_summaries_marks_missing_and_available():
    result = collect_diagnostic_summaries(
        {"pipeline.log":{"status":200,"text":"select completed"}, "arrange_report.json":{"status":404}},
    )
    assert result[0]["status"] == "available"
    assert result[1]["status"] == "missing"
    assert result[0]["content"] == "select completed"


def test_collect_diagnostic_summaries_preserves_server_errors():
    result = collect_diagnostic_summaries({"pipeline.log": {"status": 500, "error": "server error"}})
    assert result[0]["status"] == "unavailable"
    assert result[0]["error"] == "server error"


def test_aggregate_text_and_lineage_reads_script_transcript_and_clips():
    result = aggregate_text_and_lineage(
        {"script":"脚本内容"},
        [{"id":"v1","transcript_json":{"text":"字幕"},"clip_ids":[{"clip_id":"c1","asset_clip_id":"s1","source_start":1,"source_end":2,"start_time":0,"end_time":1}]}],
    )
    assert {x["artifact_type"] for x in result["text_artifacts"]} == {"script", "transcript"}
    assert result["lineage"][0]["source_clip_id"] == "s1"

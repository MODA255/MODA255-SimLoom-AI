from orchestrator.judge_evidence import build_judge_evidence


def test_build_judge_evidence_normalizes_nodes_phases_and_diagnostics():
    result = build_judge_evidence({
        "task": {"id":"t1","brief":"根据证据剪辑","query":"主题"},
        "nodes": [{"key":"select","status":"completed","input":{"clips":10},"output":{"selected":3},"calls":[{"model":"m"}],"error":None}],
        "phases": [{"phase_name":"select","status":"completed","metrics":{"selected_count":3}}],
        "diagnostics": [{"name":"select_report.json","status":"available","content":{"selected":3}}],
        "text_artifacts": [{"artifact_type":"transcript","text":"主题内容","evidence_status":"available"}],
        "lineage": [{"final_clip_id":"c1","source_clip_id":"s1"}],
    })
    assert result["status"] == "ready"
    assert result["phase_summary"][0]["key"] == "select"
    assert result["phase_summary"][0]["output"] == {"selected":3}
    assert result["diagnostics"][0]["name"] == "select_report.json"
    assert result["lineage_summary"][0]["source_clip_id"] == "s1"


def test_build_judge_evidence_marks_missing_node_diagnostics():
    result = build_judge_evidence({"task":{"id":"t1"},"nodes":[],"phases":[],"diagnostics":[],"text_artifacts":[]})
    assert result["status"] == "insufficient_evidence"
    assert result["cannot_conclude"] is True
    assert "nodes" in result["missing_evidence"]
    assert "diagnostics" in result["missing_evidence"]

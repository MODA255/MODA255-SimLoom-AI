from orchestrator.phase_rubric import evaluate_phases
from orchestrator.llm_judge import evaluate_judge_evidence


def test_phase_rubric_checks_counts_and_timeline_overlap():
    result = evaluate_phases([
        {"phase_name":"select", "status":"completed", "metrics":{"selected_count":2, "shortfall":False}, "output_data":{"selected_clip_ids":["a","b"]}},
        {"phase_name":"arrange", "status":"completed", "output_data":{"timeline":[{"start":0,"end":5},{"start":4,"end":8}]}},
        {"phase_name":"compose", "status":"completed", "metrics":{"done":1}},
        {"phase_name":"upload", "status":"completed", "output_files":["https://oss/video.mp4"]},
    ], expected_video_count=1)

    assert result["phases"]["select"]["status"] == "passed"
    assert result["phases"]["arrange"]["status"] == "warning"
    assert "TIMELINE_OVERLAP" in result["phases"]["arrange"]["failure_modes"]
    assert result["phases"]["compose"]["status"] == "passed"
    assert result["phases"]["upload"]["status"] == "passed"


def test_phase_rubric_marks_missing_phase_output_as_insufficient():
    result = evaluate_phases([{"phase_name":"select", "status":"completed"}], expected_video_count=1)
    assert result["phases"]["select"]["status"] == "insufficient_evidence"
    assert "SELECT_OUTPUT_MISSING" in result["phases"]["select"]["failure_modes"]


def test_judge_evidence_gate_returns_no_score_when_text_and_phase_evidence_missing():
    result = evaluate_judge_evidence({
        "task": {"id":"t1"},
        "phases": [{"phase_name":"select", "evidence_status":"missing"}],
        "text_artifacts": [{"artifact_type":"transcript_or_script", "evidence_status":"missing"}],
        "lineage": [],
    })
    assert result["status"] == "insufficient_evidence"
    assert result["overall_score"] is None
    assert result["confidence"] == 0
    assert result["cannot_conclude"] is True
    assert "transcript_or_script" in result["missing_evidence"]

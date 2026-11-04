from orchestrator.diagnosis import diagnose_chain


def test_diagnose_chain_maps_phase_failure_to_root_cause_and_next_action():
    result = diagnose_chain({
        "chain_id": "auto_ecom",
        "phase_rubric": {"status":"failed", "phases": {"select": {"status":"failed", "failure_modes":["NO_CLIPS_CREATED"], "evidence":["total_clips=0"]}}},
        "deterministic_rubric": {"overall":"failed", "failure_modes":["OUTPUT_MISSING"]},
        "evidence_completeness": {"status":"complete", "missing":[]},
        "cleanup": {"status":"partial", "cleanup_failures":[{"kind":"parent_task","detail":"409"}]},
    })
    assert result["status"] == "diagnosed"
    assert result["failure_stage"] == "select"
    assert result["root_cause"] == "selection_or_material_pipeline"
    assert result["next_action"]
    assert result["cleanup_status"] == "partial"


def test_diagnose_chain_refuses_root_cause_when_evidence_insufficient():
    result = diagnose_chain({
        "phase_rubric": {"status":"warning", "phases": {}},
        "deterministic_rubric": {"overall":"passed_with_warning", "failure_modes":[]},
        "evidence_completeness": {"status":"insufficient", "missing":["phase_outputs", "transcript_or_script"]},
        "cleanup": {"status":"not_started"},
    })
    assert result["status"] == "insufficient_evidence"
    assert result["cannot_conclude"] is True
    assert result["root_cause"] is None
    assert "phase_outputs" in result["missing_evidence"]

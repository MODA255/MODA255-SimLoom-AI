from orchestrator.polling import build_poll_snapshot, compute_phase_durations


def test_compute_phase_durations_uses_start_finish_and_records_errors():
    result = compute_phase_durations([
        {"phase_name":"compose","status":"completed","started_at":"2026-01-01T00:00:00Z","completed_at":"2026-01-01T00:00:05Z","error_msg":""},
        {"phase_name":"upload","status":"failed","started_at":"2026-01-01T00:00:05Z","completed_at":"2026-01-01T00:00:08Z","error_msg":"OSS error"},
    ])
    assert result["compose"]["duration_seconds"] == 5
    assert result["upload"]["duration_seconds"] == 3
    assert result["upload"]["error"] == "OSS error"


def test_build_poll_snapshot_records_status_and_progress():
    result = build_poll_snapshot({"status":"running","current_phase":"compose","progress_percentage":66.6,"progress_message":"compose","completed_phases":14,"total_phases":21}, observed_at="2026-01-01T00:00:01Z")
    assert result["status"] == "running"
    assert result["current_phase"] == "compose"
    assert result["progress_percentage"] == 66.6
    assert result["observed_at"] == "2026-01-01T00:00:01Z"

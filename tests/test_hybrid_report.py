from orchestrator.hybrid_report import build_run_report


def test_report_renders_failure_attribution_and_preflight():
    report = build_run_report({'run_id': 'attr', 'chains': [{'chain_id': 'manual_ip', 'status': 'failed', 'failure_attribution': {'category': 'business_logic_or_rule', 'confidence': 'medium', 'failure_stage': 'arrange', 'message': '无法分配可用画面', 'is_test_architecture_failure': False}, 'preflight': {'status': 'passed', 'checks': [{'name': 'api', 'status': 'passed'}]}, 'errors': []}]})
    assert '失败归因' in report['html']
    assert 'business_logic_or_rule' in report['html']
    assert 'arrange' in report['html']
    assert '创建前检查' in report['html']


    report = build_run_report({"run_id":"r1","chains":[{"chain_id":"manual_ecom","status":"passed","started_at":"2026-01-01T00:00:00Z","finished_at":"2026-01-01T00:00:10Z","phases":[{"phase_name":"compose","status":"completed","duration_ms":5000}],"errors":[],"screenshots":["chains/manual_ecom/screenshots/final.png"]}]})
    assert report["summary"]["chain_count"] == 1
    assert report["chains"][0]["duration_seconds"] == 10
    assert "compose" in report["chains"][0]["phase_durations"]
    assert "manual_ecom" in report["html"]


def test_summary_counts_warning_and_error_consistently():
    report = build_run_report({"run_id": "r3", "chains": [{"chain_id": "x", "status": "passed", "errors": ["MISSING_JUDGE_EVIDENCE"]}]})
    assert report["summary"]["chain_count"] == report["summary"]["passed"] + report["summary"]["failed"] + report["summary"]["warnings"]
    assert report["summary"]["error_count"] == 1


def test_legacy_errors_cannot_be_reported_as_passed():
    report = build_run_report({"run_id": "legacy", "chains": [{"chain_id": "x", "status": "passed", "errors": ["MISSING_JUDGE_EVIDENCE"]}]})
    assert report["summary"]["passed"] == 0
    assert report["summary"]["warnings"] == 1
    assert report["chains"][0]["status"] == "warning"
    assert "MISSING_JUDGE_EVIDENCE" in report["chains"][0]["diagnostics"][0]["message"]


def test_report_derives_elapsed_time_from_poll_events_and_explains_screenshot_gap():
    report = build_run_report({"run_id": "r4", "chains": [{"chain_id": "x", "status": "passed", "started_at": None, "finished_at": None, "poll_events": [{"observed_at": "2026-01-01T00:00:00Z", "status": "running"}, {"observed_at": "2026-01-01T00:00:08Z", "status": "completed"}], "screenshots": [], "screenshot_status": "not_collected", "errors": []}]})
    chain = report["chains"][0]
    assert chain["duration_seconds"] == 8
    assert chain["timing_source"] == "poll_events"
    assert chain["screenshot_status"] == "not_collected"
    assert "截图未采集" in report["html"]


def test_report_exposes_detailed_evidence_and_diagnostics():
    report = build_run_report({"run_id": "r2", "orchestrator_commit": "abc123", "chains": [{"chain_id": "manual_ip", "status": "failed", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:10Z", "phases": [{"phase_name": "compose", "status": "failed", "duration_ms": 5000, "error": "render timeout", "retry_count": 1}], "errors": [{"type": "timeout", "message": "render timeout", "phase": "compose"}], "screenshots": ["chains/manual_ip/screenshots/error.png"], "evidence_files": ["chains/manual_ip/diagnostics/compose.json"], "videos": [{"id": "v1", "url": "https://example.test/video/v1"}]}]})
    assert report["summary"]["error_count"] == 1
    assert report["summary"]["retry_count"] == 1
    chain = report["chains"][0]
    assert chain["diagnostics"][0]["message"] == "render timeout"
    assert chain["evidence_files"] == ["chains/manual_ip/diagnostics/compose.json"]
    assert "证据文件" in report["html"]
    assert "render timeout" in report["html"]
    assert "https://example.test/video/v1" in report["html"]
    assert report["metadata"]["orchestrator_commit"] == "abc123"


def test_report_contains_audit_sections_and_raw_evidence_details():
    report = build_run_report({"run_id": "audit", "chains": [{"chain_id": "manual_ecom", "status": "passed", "execution_mode": "manual", "scene_type": "ecom_remix", "task_id": "t1", "submission": {"recipe": "ecom_remix", "auto_delivery_enabled": False}, "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:05Z", "poll_events": [{"observed_at": "2026-01-01T00:00:00Z", "status": "running", "current_phase": "compose", "progress_percentage": 20}], "phases": [{"phase_name": "compose", "status": "completed", "started_at": "2026-01-01T00:00:01Z", "completed_at": "2026-01-01T00:00:04Z", "duration_ms": 3000, "metrics": {"clips": 3}, "input": {"a": 1}, "output": {"b": 2}}], "nodes": [{"key": "compose", "status": "completed", "input": {"a": 1}, "output": {"b": 2}}], "diagnostics": [{"name": "pipeline.log", "status": "available", "content": "compose ok"}], "screenshots": ["screenshots/task.png"], "screenshot_status": "collected", "videos": [{"id": "v1", "oss_url": "https://example.test/v1", "duration": 5}], "errors": []}]})
    html = report["html"]
    for label in ("提交参数", "轮询时间线", "阶段明细", "节点审计", "诊断文件", "截图墙", "成片信息", "证据完整性", "原始证据索引"):
        assert label in html
    assert "pipeline.log" in html
    assert "compose ok" in html
    assert "details" in html

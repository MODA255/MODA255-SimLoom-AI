from orchestrator.execution_result import classify_execution_result


def test_completed_without_video_is_failed_not_passed():
    result = classify_execution_result('manual_ecom', created={'id': 't1'}, lifecycle={'task': {'status': 'completed'}, 'videos': []})
    assert result['execution_status'] == 'completed'
    assert result['test_status'] == 'failed'
    assert result['failure']['code'] == 'OUTPUT_INCOMPLETE'


def test_blocked_has_result_without_task_id():
    result = classify_execution_result('auto_ecom', blocked_reason='AUTOMATION_ROUTE_MISSING')
    assert result['test_status'] == 'blocked'
    assert result['task_id'] is None


def test_completed_with_video_passes():
    result = classify_execution_result('manual_ecom', created={'id': 't1'}, lifecycle={'task': {'status': 'completed'}, 'videos': [{'id': 'v1'}]})
    assert result['test_status'] == 'passed'

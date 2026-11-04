from orchestrator.failure_attribution import attribute_failure


def test_business_stage_failure_has_medium_business_attribution():
    result = attribute_failure({'creation_status': 201, 'entered_running': True, 'failure_stage': 'arrange', 'remote_error': '无法分配可用画面'})
    assert result['category'] == 'business_logic_or_rule'
    assert result['confidence'] == 'medium'
    assert result['is_test_architecture_failure'] is False


def test_payload_failure_is_test_architecture():
    result = attribute_failure({'creation_status': 400, 'remote_error': 'master_asset_ids is required'})
    assert result['category'] == 'test_architecture'
    assert result['confidence'] == 'high'
    assert result['is_test_architecture_failure'] is True

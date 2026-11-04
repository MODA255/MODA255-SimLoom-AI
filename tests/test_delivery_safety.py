from orchestrator.delivery_safety import assess_delivery_safety


def test_delivery_is_insufficient_when_final_task_value_unknown():
    result = assess_delivery_safety({'auto_delivery_enabled': False}, {'auto_delivery_enabled': None})
    assert result['verdict'] == 'insufficient_evidence'


def test_delivery_passes_when_both_layers_disabled_and_no_events():
    result = assess_delivery_safety({'auto_delivery_enabled': False}, {'auto_delivery_enabled': False})
    assert result['verdict'] == 'passed'


def test_delivery_fails_when_events_exist():
    result = assess_delivery_safety({'auto_delivery_enabled': False}, {'auto_delivery_enabled': False}, [{'id': 'delivery-1'}])
    assert result['verdict'] == 'failed'

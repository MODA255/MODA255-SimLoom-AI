from orchestrator.automation_preflight import run_automation_preflight


def test_automation_preflight_requires_delivery_closed():
    result = run_automation_preflight({'execution_mode': 'automation', 'payload': {'name': 'x', 'scene_type': 'ecom_mix'}})
    assert result['status'] == 'blocked'
    assert result['blocked_reason'] == 'AUTOMATION_PAYLOAD_MISSING_FIELDS'


def test_automation_preflight_accepts_complete_payload():
    result = run_automation_preflight({'execution_mode': 'automation', 'payload': {'name': 'x', 'scene_type': 'ecom_mix', 'delivery_enabled': False}})
    assert result['status'] == 'passed'


def test_preflight_uses_search_fallback_when_source_urls_empty():
    result = run_automation_preflight({'execution_mode': 'automation', 'payload': {'name': 'x', 'scene_type': 'theme_discovery', 'delivery_enabled': False, 'source_urls': []}, 'source_search': lambda: ['https://good']}, capability_probe=lambda _: {'name': 'api', 'status': 'passed'})
    assert 'live_source_probe' in result

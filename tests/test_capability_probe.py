from orchestrator.capability_probe import probe_phase_routes


def test_probe_phase_routes_reports_unconfigured_without_remote_call():
    result = probe_phase_routes({'automation_routes': {}}, adapter=None)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'PHASE_ROUTE_UNCONFIGURED'
    assert result['routes']['automation'] == 'unconfigured'


def test_probe_phase_routes_reports_route_contracts():
    config = {'automation_routes': {
        'get_automation_stages': 'GET /automation/tasks/{task_id}/stages',
        'get_ip_promo_phases': 'GET /ip-promo-automations/{task_id}/phases'
    }}
    result = probe_phase_routes(config, adapter=None)
    assert result['status'] == 'ready'
    assert result['routes']['automation']['method'] == 'GET'
    assert result['routes']['ip_promo']['path'].endswith('/phases')

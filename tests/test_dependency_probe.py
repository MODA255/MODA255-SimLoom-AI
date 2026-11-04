from orchestrator.dependency_probe import build_dependency_probe


def test_dependency_probe_reports_capability_failure():
    class Adapter:
        def probe_capabilities(self): raise RuntimeError('offline')
    result = build_dependency_probe(Adapter(), {'payload': {'master_asset_ids': ['a']}})
    assert result[0]['status'] == 'failed'
    assert result[1]['status'] == 'passed'

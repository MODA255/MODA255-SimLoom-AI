from orchestrator.node_evidence import resolve_node_evidence


def test_nodes_404_falls_back_to_phase_evidence():
    result = resolve_node_evidence([{'phase_name': 'compose', 'status': 'completed', 'duration_ms': 10}], {'status': 404})
    assert result['status'] == 'fallback'
    assert result['source'] == 'phases_fallback'
    assert result['http_status'] == 404
    assert result['items'][0]['key'] == 'compose'


def test_nodes_empty_is_not_reported_as_missing_route():
    result = resolve_node_evidence([], {'status': 200, 'items': []})
    assert result['status'] == 'empty'
    assert result['source'] == 'nodes_api'

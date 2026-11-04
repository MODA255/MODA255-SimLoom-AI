from orchestrator.phase_tracking import collect_phase_snapshot, phase_snapshot_status


def test_collect_phase_snapshot_normalizes_parent_and_children():
    parent = {'id':'p1','status':'processing','phases':[{'name':'record','status':'completed'}]}
    children = [{'id':'c1','status':'completed','phases':[{'name':'render','status':'completed'}]}]
    snapshot = collect_phase_snapshot(parent, children, phase_fetcher=lambda task_id: {'task_id': task_id, 'phases':[{'name':'extra','status':'queued'}]})
    assert snapshot['parent']['task_id'] == 'p1'
    assert snapshot['parent']['phases'][0]['name'] == 'record'
    assert snapshot['children'][0]['task_id'] == 'c1'
    assert snapshot['children'][0]['phases'][0]['name'] == 'render'


def test_missing_phase_route_is_explicit_not_fake_success():
    snapshot = collect_phase_snapshot({'id':'p1'}, [], phase_fetcher=None)
    assert snapshot['status'] == 'unavailable'
    assert snapshot['reason'] == 'PHASE_ROUTE_UNCONFIGURED'


def test_phase_snapshot_status_detects_failed_and_stalled():
    snapshot = {'parent': {'phases':[{'name':'cut','status':'processing'}]}, 'children':[], 'status':'available'}
    result = phase_snapshot_status(snapshot, stalled_names=['cut'])
    assert result['status'] == 'stalled'
    assert result['phase'] == 'cut'

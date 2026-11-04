from orchestrator.automation_adapters import AutomationTaskAdapter, IPPromoAutomationAdapter


class Base:
    def create_daily_task(self, kind, payload, execute=False):
        assert execute is True
        return {'id': 'parent-1', 'status': 'pending', 'kind': kind}

    def get_daily_task(self, kind, task_id):
        return {'id': task_id, 'status': 'completed', 'kind': kind}

    def get_stage_snapshot(self, kind, task_id):
        return {'items': [{'name': 'compose', 'status': 'completed'}], 'kind': kind}


def test_automation_adapter_creates_and_reads_parent_task():
    adapter = AutomationTaskAdapter(Base())
    created = adapter.create({'scene_type': 'ecom_mix', 'name': 'daily'})
    assert created['id'] == 'parent-1'
    assert adapter.get('parent-1')['status'] == 'completed'
    assert adapter.stages('parent-1')['items'][0]['name'] == 'compose'


def test_ip_promo_adapter_uses_separate_kind_and_phase_route():
    adapter = IPPromoAutomationAdapter(Base())
    assert adapter.create({'name': 'ip'})['kind'] == 'ip_promo'
    assert adapter.get('parent-1')['kind'] == 'ip_promo'


def test_automation_adapter_get_propagates_route_errors_for_blocking():
    class Broken:
        def get_daily_task(self, kind, task_id): raise RuntimeError('route missing')
    adapter = AutomationTaskAdapter(Broken())
    try:
        adapter.get('p1')
    except RuntimeError as exc:
        assert 'route missing' in str(exc)
    else:
        raise AssertionError('expected route error')

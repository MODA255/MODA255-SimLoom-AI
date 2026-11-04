import pytest
from orchestrator.cleanup import CleanupCoordinator

class Adapter:
    def __init__(self, fail=None): self.calls=[]; self.fail=fail
    def cleanup_resource(self, kind, resource_id):
        self.calls.append((kind, resource_id))
        if kind == self.fail: raise RuntimeError('boom')
        return {'status':'deleted'}


def refs():
    return {'parent_task_id':'p','session_ids':['s'],'segment_ids':['g'],'child_task_ids':['c'],'task_id':'t','asset_ids':['a'],'dynamic_assets':True}


def test_cleanup_runs_in_order_and_returns_evidence():
    adapter=Adapter(); result=CleanupCoordinator(adapter).cleanup(refs())
    assert [x[0] for x in adapter.calls] == ['child_task','segment','session','parent_task','task','asset']
    assert result['cleanup_status'] == 'passed'
    assert result['orphan_resource_ids'] == []


def test_cleanup_continues_after_failure_and_records_orphan():
    adapter=Adapter(fail='session'); result=CleanupCoordinator(adapter).cleanup(refs())
    assert ('parent_task','p') in adapter.calls
    assert result['cleanup_status'] == 'partial'
    assert any(x['kind'] == 'session' for x in result['cleanup_failures'])
    assert 's' in result['orphan_resource_ids']


def test_cleanup_is_idempotent_for_missing_refs():
    result=CleanupCoordinator(Adapter()).cleanup({})
    assert result['cleanup_status'] == 'skipped'

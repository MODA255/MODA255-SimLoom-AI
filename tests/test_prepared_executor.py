from orchestrator.prepared_executor import run_prepared_chain


def test_prepared_executor_does_not_call_runner_when_preflight_blocks():
    called = []
    class Adapter:
        def probe_capabilities(self): raise RuntimeError('offline')
    result = run_prepared_chain({'chain_id': 'manual_ecom', 'execution_mode': 'manual', 'payload': {}}, Adapter(), lambda c: called.append(c))
    assert result['test_status'] == 'blocked'
    assert called == []


def test_prepared_executor_attaches_preflight_to_successful_run():
    class Adapter:
        def probe_capabilities(self): return {}
    result = run_prepared_chain({'chain_id': 'manual_ecom', 'execution_mode': 'manual', 'payload': {'recipe': 'ecom_remix', 'master_asset_ids': ['a'], 'project_name': 'x', 'num_versions': 1, 'bgm_mode': 'none', 'auto_delivery_enabled': False}}, Adapter(), lambda c: {'poll_status': 'completed'})
    assert result['preflight']['status'] == 'passed'

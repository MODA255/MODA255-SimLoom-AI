from orchestrator.chain_runners import build_chain_runner


def test_chain_runner_invokes_preflight_before_manual_creation():
    calls = []
    class Adapter:
        def probe_capabilities(self): raise RuntimeError('offline')
        def create_vfc_task(self, payload): calls.append(payload); return {'id': 't'}
    result = build_chain_runner(Adapter(), {'chain_id': 'manual_ecom', 'execution_mode': 'manual', 'payload': {}})
    assert result['test_status'] == 'blocked'
    assert calls == []


def test_manual_runner_collects_videos_after_completion():
    class WithVideos:
        def probe_capabilities(self): return {}
        def create_vfc_task(self, payload): return {'id': 't1'}
        def get_task(self, task_id): return {'id': task_id, 'status': 'completed'}
        def get_videos(self, task_id): return [{'id': 'v1'}]
    result = build_chain_runner(WithVideos(), {'chain_id': 'manual_ecom', 'execution_mode': 'manual', 'payload': {'recipe': 'ecom_remix', 'master_asset_ids': ['a'], 'project_name': 'x', 'num_versions': 1, 'bgm_mode': 'none', 'auto_delivery_enabled': False}, 'poll_interval_seconds': 0})
    assert result['videos'] == [{'id': 'v1'}]

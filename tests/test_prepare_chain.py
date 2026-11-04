from orchestrator.prepare_chain import prepare_chain


def test_prepare_chain_blocks_before_creation_when_probe_fails():
    class Adapter:
        def probe_capabilities(self): raise RuntimeError('offline')
    result = prepare_chain(Adapter(), {'execution_mode': 'manual', 'payload': {}})
    assert result['status'] == 'blocked'
    assert result['failure_attribution']['category'] == 'environment_or_dependency'

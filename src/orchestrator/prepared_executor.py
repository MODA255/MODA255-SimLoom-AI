from .prepare_chain import prepare_chain
from .failure_attribution import attribute_failure


def run_prepared_chain(chain, adapter, runner):
    preflight = prepare_chain(adapter, chain)
    if preflight['status'] != 'passed':
        return {'chain_id': chain.get('chain_id'), 'execution_status': 'not_started', 'test_status': 'blocked', 'preflight': preflight, 'failure_attribution': preflight.get('failure_attribution')}
    try:
        raw = runner(chain)
        if raw.get('poll_status') == 'completed' and (raw.get('task') or {}).get('status') == 'failed':
            raw['failure_attribution'] = attribute_failure({'creation_status': 201, 'entered_running': True, 'failure_stage': (raw.get('task') or {}).get('current_phase'), 'remote_error': (raw.get('task') or {}).get('error_msg'), 'evidence': raw.get('poll_events')})
        raw['preflight'] = preflight
        return raw
    except Exception as exc:
        return {'chain_id': chain.get('chain_id'), 'execution_status': 'failed', 'test_status': 'failed', 'preflight': preflight, 'failure_attribution': attribute_failure({'remote_error': str(exc)})}

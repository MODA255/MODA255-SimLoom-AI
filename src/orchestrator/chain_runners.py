from .lifecycle import run_new_chain
from .automation_adapters import AutomationTaskAdapter, IPPromoAutomationAdapter
from .automation_preflight import run_automation_preflight
from .automation_lifecycle import run_automation_parent_child
from .prepared_executor import run_prepared_chain


def _run_manual(adapter, chain, *, ledger=None, progress_callback=None):
    result = run_new_chain(adapter, chain, timeout_seconds=chain.get('timeout_seconds', 3600), interval_seconds=chain.get('poll_interval_seconds', 20), ledger=ledger, progress_callback=progress_callback)
    task_id = result.get('task_id')
    if task_id and hasattr(adapter, 'get_videos'):
        result['videos'] = adapter.get_videos(task_id)
    return result


def build_chain_runner(adapter, chain, *, ledger=None):
    mode = chain.get('execution_mode', 'manual')
    if mode == 'manual':
        result = run_prepared_chain(chain, adapter, lambda c: _run_manual(adapter, c, ledger=ledger, progress_callback=chain.get('progress_callback')))
        return result
    lifecycle_adapter = IPPromoAutomationAdapter(adapter) if chain.get('scene_type') == 'ip_promo' else AutomationTaskAdapter(adapter)
    automation_preflight = run_automation_preflight(chain)
    if automation_preflight['status'] != 'passed':
        return {'chain_id': chain.get('chain_id'), 'execution_status': 'not_started', 'test_status': 'blocked', 'blocked_reason': automation_preflight['blocked_reason'], 'preflight': automation_preflight}
    try:
        created = lifecycle_adapter.create(chain.get('payload') or {})
    except Exception as exc:
        return {'blocked_reason': 'AUTOMATION_CREATE_FAILED', 'error': f'{type(exc).__name__}: {exc}'}
    task_id = created.get('id') or created.get('task_id')
    if not task_id:
        return {'blocked_reason': 'AUTOMATION_CREATE_TASK_ID_MISSING', 'creation_response': created}
    callbacks = []
    if ledger:
        callbacks.append(lambda event: ledger.append_poll(chain['chain_id'], event))
    if chain.get('progress_callback'):
        callbacks.append(chain['progress_callback'])

    def on_event(event):
        for callback in callbacks:
            callback(event)

    lifecycle = lifecycle_adapter.poll(
        task_id,
        timeout_seconds=chain.get('timeout_seconds', 10800),
        interval_seconds=chain.get('poll_interval_seconds', 20),
        on_event=on_event if callbacks else None,
    )
    lifecycle['creation_response'] = created
    if lifecycle.get('poll_status') == 'timeout':
        lifecycle['test_status'] = 'inconclusive'
        lifecycle['execution_status'] = 'inconclusive'
        lifecycle['poll_timeout'] = True
    return lifecycle

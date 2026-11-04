from .runner import poll_task
from .execution_ledger import ExecutionLedger


def run_new_chain(adapter, spec, *, interval_seconds=20, timeout_seconds=3600, sleep=None, ledger=None, progress_callback=None):
    submission = dict(spec.get('payload') or {})
    created = adapter.create_vfc_task(submission)
    task_id = created.get('id') or created.get('task_id')
    if not task_id:
        raise RuntimeError('CREATE_TASK_ID_MISSING')
    if ledger:
        ledger.write_snapshot(spec.get('chain_id', 'unknown'), 'submission.json', submission)
        ledger.write_snapshot(spec.get('chain_id', 'unknown'), 'creation-response.json', created)
    kwargs = {'timeout_seconds': spec.get('timeout_seconds', timeout_seconds), 'interval_seconds': spec.get('poll_interval_seconds', interval_seconds)}
    if ledger:
        kwargs['on_event'] = lambda event: ledger.append_poll(spec.get('chain_id', 'unknown'), event)
    if progress_callback:
        prior = kwargs.get('on_event')
        def on_event(event):
            if prior:
                prior(event)
            progress_callback({'step': 'processing', **event})
        kwargs['on_event'] = on_event
    if sleep is not None:
        kwargs['sleep'] = sleep
    lifecycle = poll_task(adapter, task_id, **kwargs)
    events = lifecycle['poll_events']
    return {
        'chain_id': spec.get('chain_id'),
        'task_id': task_id,
        'submission': submission,
        'creation_response': created,
        **lifecycle,
        'started_at': events[0].get('observed_at') if events else None,
        'finished_at': events[-1].get('observed_at') if events else None,
    }

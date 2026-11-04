from orchestrator.runner import poll_task


class Adapter:
    def __init__(self):
        self.items = iter([
            {'id': 't1', 'status': 'running', 'current_phase': 'compose'},
            {'id': 't1', 'status': 'completed', 'current_phase': 'completed'},
        ])

    def get_task(self, task_id):
        return next(self.items)


def test_poll_task_keeps_all_events_until_terminal():
    result = poll_task(Adapter(), 't1', interval_seconds=0, sleep=lambda _: None)
    assert result['poll_status'] == 'completed'
    assert len(result['poll_events']) == 2
    assert result['poll_events'][0]['status'] == 'running'
    assert result['poll_events'][-1]['status'] == 'completed'


def test_poll_task_returns_timeout_evidence():
    class Running:
        def get_task(self, task_id):
            return {'id': task_id, 'status': 'running'}
    ticks = iter([0, 0, 2])
    result = poll_task(Running(), 't1', timeout_seconds=1, interval_seconds=0, sleep=lambda _: None, clock=lambda: next(ticks))
    assert result['poll_status'] == 'timeout'
    assert result['poll_error'] == 'POLL_TIMEOUT'
    assert result['poll_events']

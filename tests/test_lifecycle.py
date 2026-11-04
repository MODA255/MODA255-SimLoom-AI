from orchestrator.lifecycle import run_new_chain


class Adapter:
    def __init__(self):
        self.created = []
        self.states = iter([
            {'id': 'new-task', 'status': 'running', 'current_phase': 'compose'},
            {'id': 'new-task', 'status': 'completed', 'current_phase': 'completed'},
        ])

    def create_vfc_task(self, payload):
        self.created.append(payload)
        return {'id': 'new-task', 'status': 'queued'}

    def get_task(self, task_id):
        return next(self.states)


def test_run_new_chain_records_submission_and_poll_lifecycle():
    result = run_new_chain(Adapter(), {'chain_id': 'manual_ecom', 'payload': {'recipe': 'ecom_remix'}}, interval_seconds=0, sleep=lambda _: None)
    assert result['task_id'] == 'new-task'
    assert result['submission']['recipe'] == 'ecom_remix'
    assert [event['status'] for event in result['poll_events']] == ['running', 'completed']
    assert result['started_at'] == result['poll_events'][0]['observed_at']
    assert result['finished_at'] == result['poll_events'][-1]['observed_at']


def test_run_new_chain_writes_live_ledger_when_configured(tmp_path):
    from orchestrator.execution_ledger import ExecutionLedger
    ledger = ExecutionLedger(tmp_path)
    run_new_chain(Adapter(), {'chain_id': 'manual_ecom', 'payload': {'recipe': 'ecom_remix'}}, interval_seconds=0, sleep=lambda _: None, ledger=ledger)
    assert (tmp_path / 'manual_ecom' / 'submission.json').exists()
    assert len((tmp_path / 'manual_ecom' / 'poll-events.jsonl').read_text().splitlines()) == 2
    adapter = Adapter()
    result = run_new_chain(adapter, {'chain_id': 'manual_ecom', 'payload': {'recipe': 'ecom_remix'}, 'timeout_seconds': 17, 'poll_interval_seconds': 3}, sleep=lambda _: None)
    assert result['poll_status'] == 'completed'
    assert len(result['poll_events']) == 2

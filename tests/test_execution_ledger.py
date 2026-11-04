from orchestrator.execution_ledger import ExecutionLedger


def test_ledger_appends_poll_events_without_overwriting(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    ledger.append_poll('manual_ecom', {'status': 'running'})
    ledger.append_poll('manual_ecom', {'status': 'completed'})
    lines = (tmp_path / 'manual_ecom' / 'poll-events.jsonl').read_text().splitlines()
    assert len(lines) == 2


def test_ledger_writes_final_result(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    result = ledger.write_result('auto_ecom', {'test_status': 'blocked'})
    assert result['test_status'] == 'blocked'
    assert (tmp_path / 'auto_ecom' / 'execution-result.json').exists()

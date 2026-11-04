from orchestrator.suite_executor import run_suite_isolated
from orchestrator.execution_ledger import ExecutionLedger


def test_suite_marks_runner_block_reason_as_blocked(tmp_path):
    result = run_suite_isolated([{'chain_id': 'auto_ecom'}], lambda chain: {'blocked_reason': 'ROUTE_MISSING'}, ExecutionLedger(tmp_path))
    assert result['status_counts']['blocked'] == 1
    assert result['results'][0]['blocked']['code'] == 'ROUTE_MISSING'

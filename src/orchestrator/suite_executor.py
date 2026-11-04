from orchestrator.execution_ledger import ExecutionLedger
from orchestrator.execution_result import classify_execution_result


def run_chain_isolated(chain, runner, ledger):
    chain_id = chain.get('chain_id', 'unknown')
    try:
        raw = runner(chain)
        result = classify_execution_result(chain_id, created=raw.get('creation_response'), lifecycle=raw, blocked_reason=raw.get('blocked_reason'), expected_video_count=chain.get('expected_video_count', 1))
        result['raw'] = raw
    except Exception as exc:
        result = classify_execution_result(chain_id, error=exc)
    return ledger.write_result(chain_id, result)


def run_suite_isolated(chains, runner, ledger):
    results = []
    for chain in chains:
        results.append(run_chain_isolated(chain, runner, ledger))
    return {'chain_count': len(results), 'results': results, 'status_counts': {status: sum(1 for r in results if r.get('test_status') == status) for status in ('passed', 'failed', 'blocked', 'warning', 'not_run')}}

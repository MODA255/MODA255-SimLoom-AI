import json
from pathlib import Path

from orchestrator.phase1_ingest_runners import run_link_ingest
from orchestrator.phase1_runner import _resolve_case_timeout
from orchestrator.execution_ledger import ExecutionLedger


def test_timeout_precedence_case_phase_global_default():
    assert _resolve_case_timeout({'timeout_seconds': 7}, {'timeout_seconds': 11}, {'timeout_seconds': 13}) == 7
    assert _resolve_case_timeout({}, {'timeout_seconds': 11}, {'timeout_seconds': 13}) == 11
    assert _resolve_case_timeout({}, {}, {'timeout_seconds': 13}) == 13
    assert _resolve_case_timeout({}, {}, {}) == 1800


def test_link_missing_id_matches_list_assets_until_timeout(monkeypatch, tmp_path):
    class Adapter:
        def ingest_url(self, payload):
            return {'status': 'accepted'}
        def list_assets(self, folder_id):
            return [{'id': 'asset-1', 'folder_id': folder_id, 'source_url': 'https://example.test/a'}]
        def get_asset(self, asset_id):
            return {'id': asset_id, 'status': 'ready'}
        def get_asset_progress(self, asset_id):
            return {'id': asset_id, 'status': 'ready'}

    result = run_link_ingest(Adapter(), {
        'chain_id': 'I-LINK', 'source_url': 'https://example.test/a', 'folder_id': 'folder',
        'timeout_seconds': 0, 'poll_interval_seconds': 0,
    }, tmp_path)
    assert result['submit']['accepted'] is True
    assert result['poll']['status'] == 'ready'
    assert result['downstream_ready'] is True


def test_timeout_is_inconclusive_and_final_report_contains_case(tmp_path):
    from orchestrator.phase1_report import write_phase1_report
    run = {'run_id': 'r1', 'status': 'completed', 'case_runs': [
        {'case_run_id': 'c1', 'chain_id': 'I-LINK', 'status': 'inconclusive', 'hard_acceptance': False,
         'result': {'test_status': 'inconclusive', 'failure': {'code': 'ASSET_TIMEOUT'}}}
    ]}
    files = write_phase1_report(run, tmp_path, run=run)
    report = json.loads(Path(files['daily_report']).read_text())
    assert report['counts']['inconclusive'] == 1
    assert (tmp_path / 'r1' / 'cases' / 'c1' / 'case.json').exists()


def test_each_case_writes_realtime_progress_snapshot(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    ledger.write_snapshot('case-1', 'progress.json', {'status': 'running', 'step': 'submit'})
    assert json.loads((tmp_path / 'case-1' / 'progress.json').read_text())['step'] == 'submit'


def test_link_accepted_without_id_deadline_is_inconclusive(tmp_path):
    class Adapter:
        def ingest_url(self, payload): return {'status': 'accepted'}
        def list_assets(self, folder_id): return []
    result = run_link_ingest(Adapter(), {'chain_id': 'no-id', 'source_url': 'https://x', 'folder_id': 'f', 'timeout_seconds': 0, 'poll_interval_seconds': 0}, tmp_path)
    assert result['accept']['status'] == 'inconclusive'
    assert result['failure']['code'] == 'NO_UNIQUE_MATCH'



def test_automation_poll_forwards_progress_callback_and_timeout_snapshot(monkeypatch):
    from orchestrator import chain_runners

    class Adapter:
        def create_daily_task(self, kind, payload, execute=False):
            return {'id': 'automation-1'}

        def get_daily_task(self, kind, task_id):
            return {'id': task_id, 'status': 'processing', 'percent': 12}

    monkeypatch.setattr(chain_runners, 'run_automation_preflight', lambda chain: {'status': 'passed'})
    events = []
    result = chain_runners.build_chain_runner(Adapter(), {
        'execution_mode': 'automation', 'scene_type': 'ecom_mix',
        'payload': {'scene_type': 'ecom_mix'}, 'timeout_seconds': 0,
        'poll_interval_seconds': 0, 'progress_callback': events.append,
    })
    assert events and events[0]['progress_percentage'] is None
    assert result['test_status'] == 'inconclusive'
    assert result['poll_timeout'] is True
    assert result['poll_events'][-1]['status'] == 'processing'


def test_phase_chain_summary_preserves_inconclusive():
    from orchestrator.phase1_runner import _summarize_chain_statuses
    assert _summarize_chain_statuses(['inconclusive', 'failed']) == 'inconclusive'
    assert _summarize_chain_statuses(['poll_timeout']) == 'inconclusive'
    assert _summarize_chain_statuses(['blocked', 'blocked']) == 'blocked'


def test_link_polling_writes_intermediate_progress_snapshot(tmp_path):
    class Adapter:
        def ingest_url(self, payload): return {'status': 'accepted'}
        def list_assets(self, folder_id): return [{'id': 'a', 'folder_id': folder_id, 'source_url': 'https://x'}]
        def get_asset(self, asset_id): return {'id': asset_id, 'status': 'pending'}
        def get_asset_progress(self, asset_id): return {'id': asset_id, 'status': 'processing', 'percent': 10}
    result = run_link_ingest(Adapter(), {'chain_id': 'progress', 'source_url': 'https://x', 'folder_id': 'f', 'timeout_seconds': 0, 'poll_interval_seconds': 0}, tmp_path)
    saved = json.loads((tmp_path / 'progress' / 'ingest-result.json').read_text())
    assert saved['progress']['step'] == 'asset_status'
    assert saved['progress_history']

import json
from pathlib import Path

from orchestrator.automation import classify_automation_scope, normalize_automation_result
from orchestrator.daily import DailyRunner
from orchestrator.material_ingest import MaterialRecord, ingest_and_wait_ready, normalize_material_record


ROOT = Path(__file__).parents[1]


def test_daily_runner_loads_matrixgen_source_policy_file(tmp_path):
    class Engine:
        config = type('Config', (), {'mode': 'simulation', 'data': {}, 'snapshot': lambda self: {}})()
        repo = None

    runner = DailyRunner(Engine())
    expected = json.loads((ROOT / 'configs/matrixgen-source-policy.json').read_text())
    assert runner.policy.data == expected
    assert runner.policy.config_path == ROOT / 'configs/matrixgen-source-policy.json'


def test_material_ingest_exposes_standard_material_record():
    raw = {'id': 'a1', 'title': 'Demo', 'source_url': 'https://example.test/v', 'folder_id': 'f1', 'status': 'ready'}
    record = normalize_material_record(raw, source_type='link')
    assert isinstance(record, MaterialRecord)
    assert record.asset_id == 'a1'
    assert record.source_url == raw['source_url']
    assert record.status == 'ready'


def test_material_ingest_result_contains_compatible_record():
    class Adapter:
        def ingest_url(self, payload): return {'id': 'a1'}
        def list_assets(self, folder_id): return {'items': [{'id': 'a1', 'source_url': 'u', 'status': 'ready'}]}
        def get_asset(self, asset_id): return {'id': asset_id, 'source_url': 'u', 'status': 'ready'}
        def get_asset_progress(self, asset_id): return {'status': 'ready'}

    result = ingest_and_wait_ready(Adapter(), {'url': 'u'}, 'f', 'u', timeout=0, poll=0)
    assert isinstance(result['record'], MaterialRecord)
    assert result['record'].asset_id == 'a1'


def test_normalize_automation_result_separates_scopes_and_ip_sync_block():
    assert classify_automation_scope({'creation_source': 'manual_vfc'}) == 'manual_vfc'
    assert classify_automation_scope({'creation_source': 'automation_task'}) == 'automation_task'
    assert classify_automation_scope({'creation_source': 'ip_promo_automation'}) == 'ip_promo_automation'
    result = normalize_automation_result({'creation_source': 'ip_promo_automation', 'clip_status': 'clip_done', 'sync_status': 'blocked'})
    assert result['automation_scope'] == 'ip_promo_automation'
    assert result['status'] == 'blocked'
    assert result['reason'] == 'IP_CLIP_DONE_SYNC_BLOCKED'

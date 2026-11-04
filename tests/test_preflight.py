from orchestrator.preflight import run_preflight


def test_preflight_blocks_missing_manual_payload_fields():
    result = run_preflight({'execution_mode': 'manual', 'payload': {'recipe': 'ecom_remix'}})
    assert result['status'] == 'blocked'
    assert 'master_asset_ids' in result['missing_fields']


def test_preflight_passes_valid_manual_payload_and_probe():
    result = run_preflight({'execution_mode': 'manual', 'payload': {'recipe': 'ecom_remix', 'master_asset_ids': ['a'], 'project_name': 'x', 'num_versions': 1, 'bgm_mode': 'none', 'auto_delivery_enabled': False}}, capability_probe=lambda c: {'name': 'api', 'status': 'passed'}, asset_probe=lambda c: {'name': 'asset', 'status': 'passed'})
    assert result['status'] == 'passed'

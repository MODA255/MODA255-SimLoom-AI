import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[1]


def test_full_suite_config_has_six_chains_and_profiles():
    data = json.loads((ROOT / 'configs' / 'daily-full-suite.json').read_text())
    assert [c['chain_id'] for c in data['chains']] == [
        'manual_ecom', 'manual_auto_theme', 'manual_ip_promo',
        'auto_ecom', 'auto_theme', 'auto_ip',
    ]
    assert all(c['template_id'] for c in data['chains'])
    assert all(c['feature_profiles'] == ['F0'] or 'F0' in c['feature_profiles'] for c in data['chains'])
    assert data['chains'][2]['material_policy'] == 'multi-material'


def test_daily_full_plan_has_six_cases_and_distribution(tmp_path):
    config = ROOT / 'configs' / 'example.json'
    result = subprocess.run([
        sys.executable, '-m', 'orchestrator', 'daily-full-plan', '--config', str(config),
        '--input-json', json.dumps({'date': '2025-01-01', 'source_rotation_index': 0}),
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload['cases']) == 6
    assert set(payload['profile_distribution']) == {'F0'}
    assert payload['plan_only'] is True
    assert payload['remote_write_performed'] is False


def test_full_plan_ip_precondition_and_missing_input_blocked(tmp_path):
    from orchestrator.full_suite import build_full_plan
    out = build_full_plan(ROOT / 'configs' / 'daily-full-suite.json', {'date': '2025-01-01'})
    ip = next(c for c in out['cases'] if c['chain_id'] == 'manual_ip_promo')
    assert ip['status'] == 'blocked'
    assert ip['blocked_reason']
    assert all(c['status'] == 'blocked' for c in out['cases'])


def test_full_plan_rotation_and_not_applicable_and_partial_report():
    from orchestrator.full_suite import build_full_plan, full_report
    a = build_full_plan(ROOT / 'configs' / 'daily-full-suite.json', {
        'date': '2025-01-01', 'source_rotation_index': 0,
        'sources': {'douyin_video': [{'asset_id': 'a', 'status': 'ready'}], 'douyin_live': [{'asset_id': 'l', 'status': 'ready'}]},
        'feature_profiles': ['F0', 'F1'], 'not_applicable': ['manual_auto_theme'],
    })
    b = build_full_plan(ROOT / 'configs' / 'daily-full-suite.json', {
        'date': '2025-01-02', 'source_rotation_index': 1,
        'sources': {'douyin_video': [{'asset_id': 'b', 'status': 'ready'}], 'douyin_live': [{'asset_id': 'm', 'status': 'ready'}]},
    })
    assert a['cases'][0]['source_type'] != b['cases'][0]['source_type']
    assert next(c for c in a['cases'] if c['chain_id'] == 'manual_auto_theme')['status'] == 'not_applicable'
    assert full_report({**a, 'cases': [{**c, 'status': 'passed' if c['chain_id'] == 'manual_ecom' else c['status']} for c in a['cases']]})['overall'] == 'partial'

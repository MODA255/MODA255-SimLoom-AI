import json
from pathlib import Path

import pytest

from orchestrator.automation import normalize_automation_result, automation_templates, evaluate_automation_status
from orchestrator.material_ingest import MaterialRecord
from orchestrator.policy import SourcePolicy


def test_policy_loads_config_and_supports_video_types():
    policy = SourcePolicy.load()
    assert policy.config_path.name == 'matrixgen-source-policy.json'
    assert SourcePolicy.load().config_path == policy.config_path
    for source_type in ('douyin_video', 'douyin_live', 'weibo_video'):
        assert policy.source_type_allowed(source_type)


def test_policy_loader_accepts_explicit_path_and_returns_independent_snapshot(tmp_path):
    config = tmp_path / 'policy.json'
    config.write_text(json.dumps({'content': {'rules': {}, 'allowed_url_types': []}}))
    first = SourcePolicy.load(config)
    first.data['content']['rules']['mutated'] = []
    second = SourcePolicy.load(config)
    assert 'mutated' not in second.data['content']['rules']


def test_asset_record_normalizes_status_and_progress_contract():
    record = MaterialRecord.from_asset({'id': 'a1', 'url': 'https://x/a', 'folder_id': 'f', 'status': 'processing'}, {'percent': 42})
    assert record.to_dict() == {
        'asset_id': 'a1', 'source_url': 'https://x/a', 'folder_id': 'f',
        'status': 'processing', 'progress': {'percent': 42}, 'clip_count': 0,
        'asr': None, 'ocr': None, 'vector': None, 'tags': [],
    }


def test_material_record_has_standard_asset_contract():
    record = MaterialRecord.from_asset({'id': 'a1', 'source_url': 'https://x/a', 'folder_id': 'f', 'status': 'active'})
    assert record.asset_id == 'a1'
    assert record.status == 'ready'
    assert record.progress is not None
    assert record.to_dict()['source_url'] == 'https://x/a'


def test_automation_normalization_and_status_matrix():
    assert set(automation_templates()) >= {'ecom_mix', 'theme_discovery', 'ip_promo'}
    result = normalize_automation_result({'parent': {'status': 'completed'}, 'children': [{'status': 'completed'}], 'video': {'status': 'completed'}, 'delivery': {'status': 'closed'}, 'kind': 'ecom_mix'})
    assert evaluate_automation_status(result).status == 'passed'
    manual = normalize_automation_result({'parent': {'status': 'completed', 'creation_source': 'manual_vfc'}, 'children': [], 'video': {'status': 'completed'}, 'delivery': {'status': 'closed'}, 'kind': 'ecom_mix'})
    assert evaluate_automation_status(manual).status == 'blocked'

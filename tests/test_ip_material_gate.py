import pytest
from orchestrator.ip_material_gate import validate_ip_materials


def asset(i, duration=20, clips=4, ip='IP-A', asr=True, evidence=True, status='active'):
    return {'asset_id': f'a{i}', 'duration': duration, 'clip_count': clips, 'ip_name': ip,
            'asr': {'text': 'spoken'} if asr else None,
            'ip_evidence': {'matched': True} if evidence else None, 'status': status}


def test_accepts_qualified_ip_pool():
    result = validate_ip_materials([asset(i, duration=15, clips=4) for i in range(9)], automated=True)
    assert result['valid'] is True


def test_rejects_insufficient_ip_pool_before_task_creation():
    result = validate_ip_materials([asset(i) for i in range(2)], automated=True)
    assert result['valid'] is False
    assert result['code'] == 'IP_MATERIAL_INSUFFICIENT'


def test_rejects_mismatched_or_unready_material():
    assets = [asset(i, ip='IP-A' if i < 2 else 'IP-B') for i in range(3)]
    assets[0]['status'] = 'processing'
    result = validate_ip_materials(assets, automated=False)
    assert result['valid'] is False
    assert 'MATERIAL_NOT_READY' in result['reasons'] or 'IP_EVIDENCE_MISMATCH' in result['reasons']


def test_manual_profile_uses_lower_thresholds_but_requires_evidence():
    result = validate_ip_materials([asset(i, duration=25, clips=1) for i in range(3)], automated=False)
    assert result['valid'] is True
    assert result['profile'] == 'manual_ip_promo'

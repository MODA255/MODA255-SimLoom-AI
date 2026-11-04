from orchestrator.evidence_normalizer import normalize_chain_evidence


def test_normalizer_marks_unsubmitted_automation_blocked():
    result = normalize_chain_evidence({'chain_id': 'auto_ecom', 'execution_mode': 'automation', 'blocked_reason': 'CREATE_NOT_IMPLEMENTED'})
    assert result.verdict.test_status == 'blocked'
    assert result.execution.status == 'not_started'
    assert result.verdict.business_verdict == 'not_applicable'


def test_normalizer_uses_phase_fallback_and_delivery_unknown():
    result = normalize_chain_evidence({'chain_id': 'manual_ecom', 'execution_mode': 'manual', 'payload': {'auto_delivery_enabled': False}, 'task': {'id': 't1', 'status': 'completed', 'auto_delivery_enabled': None}, 'phases': [{'phase_name': 'compose', 'status': 'completed'}], 'nodes_response': {'status': 404}, 'diagnostics': {'pipeline.log': {'status': 404}}, 'videos': [{'id': 'v1'}]})
    assert result.evidence.node_source == 'phases_fallback'
    assert result.delivery_safety.verdict == 'insufficient_evidence'
    assert result.verdict.business_verdict == 'insufficient_evidence'


def _safe_delivery():
    return {
        'delivery_enabled': False, 'auto_delivery_enabled': False,
        'advertiser_id': None, 'awemes': [], 'products': [],
        'promotion_goal': None, 'adlab_scene': None,
    }


def test_completed_legacy_entry_requires_strict_phase1_evidence():
    result = normalize_chain_evidence({
        'chain_id': 'legacy', 'execution_status': 'completed',
        'submission': {'accepted': True, 'task_id': 't1'},
        'delivery_before': _safe_delivery(), 'delivery_after': _safe_delivery(),
        'phase_integrity': True,
        # business_output is deliberately absent: completed must not bypass phase1.
    })
    assert result.verdict.test_status == 'inconclusive'
    assert result.verdict.business_verdict == 'insufficient_evidence'


def test_phase1_acceptance_status_and_failure_are_respected():
    result = normalize_chain_evidence({
        'execution_status': 'completed',
        'phase1_acceptance': {'status': 'failed', 'failure': 'business assertion failed'},
        'submission': {'accepted': True, 'task_id': 't1'},
        'delivery_before': _safe_delivery(), 'delivery_after': _safe_delivery(),
        'phase_integrity': True, 'business_output': True,
    })
    assert result.verdict.test_status == 'failed'
    assert result.verdict.issues == [{'failure': 'business assertion failed'}]


def test_lifecycle_status_maps_to_execution_status():
    result = normalize_chain_evidence({
        'lifecycle_status': 'completed', 'submission': {},
    })
    assert result.execution.status == 'completed'
    assert result.verdict.execution_status == 'completed'


import pytest


@pytest.mark.parametrize('status', ['poll_timeout', 'timeout', 'failed', 'cancelled', 'cleanup_failed'])
def test_terminal_execution_failures_have_uniform_failed_verdict(status):
    result = normalize_chain_evidence({'execution_status': status})

    assert result.verdict.test_status == 'failed'
    assert result.verdict.business_verdict == 'fail'
    assert result.verdict.issues == [{'failure': f'execution_status={status}'}]


@pytest.mark.parametrize('status', ['poll_timeout', 'timeout', 'failed', 'cancelled', 'cleanup_failed'])
def test_execution_failure_cannot_be_overridden_by_phase1_acceptance(status):
    result = normalize_chain_evidence({
        'execution_status': status,
        'phase1_acceptance': {'status': 'passed'},
    })

    assert result.verdict.test_status == 'failed'
    assert result.verdict.business_verdict == 'fail'

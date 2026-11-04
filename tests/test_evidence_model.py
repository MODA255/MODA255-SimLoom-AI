from orchestrator.evidence_model import ChainEvidence


def test_chain_evidence_separates_blocked_from_failed():
    item = ChainEvidence(chain_id='auto_ecom', execution_mode='automation')
    item.execution.status = 'not_started'
    item.verdict.test_status = 'blocked'
    item.verdict.business_verdict = 'not_applicable'
    assert item.execution.status == 'not_started'
    assert item.verdict.test_status == 'blocked'
    assert item.verdict.business_verdict == 'not_applicable'


def test_chain_evidence_serializes_delivery_and_fallback_source():
    item = ChainEvidence(chain_id='manual_ecom', execution_mode='manual', scene_type='ecom_remix')
    item.evidence.node_source = 'phases_fallback'
    item.delivery_safety.verdict = 'insufficient_evidence'
    data = item.model_dump()
    assert data['evidence']['node_source'] == 'phases_fallback'
    assert data['delivery_safety']['verdict'] == 'insufficient_evidence'

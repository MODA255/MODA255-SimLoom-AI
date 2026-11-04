from .evidence_model import ChainEvidence, ExecutionState, EvidenceState, DeliverySafety, ChainVerdict
from .node_evidence import resolve_node_evidence
from .diagnostics import classify_diagnostics
from .delivery_safety import assess_delivery_safety


def normalize_chain_evidence(raw):
    raw = raw or {}
    task = raw.get('task') or {}
    nodes = resolve_node_evidence(raw.get('phases') or [], raw.get('nodes_response', raw.get('nodes') or []))
    diagnostics = classify_diagnostics(raw.get('diagnostics') or {}) if isinstance(raw.get('diagnostics'), dict) else raw.get('diagnostics') or []
    videos = raw.get('videos') or []
    delivery = assess_delivery_safety(raw.get('submission') or raw.get('payload'), task, raw.get('delivery_events'))

    lifecycle_status = raw.get('lifecycle_status')
    execution_status = raw.get('execution_status') or task.get('status') or lifecycle_status or ('not_started' if raw.get('blocked_reason') else 'unknown')
    original_execution_status = execution_status
    execution_status = {'poll_timeout': 'timeout', 'cleanup_failed': 'failed'}.get(execution_status, execution_status)
    failure_status = original_execution_status in {'poll_timeout', 'timeout', 'failed', 'cancelled', 'cleanup_failed'} or raw.get('poll_status') == 'timeout'
    test_status = 'blocked' if raw.get('blocked_reason') else 'failed' if raw.get('execution_error') or failure_status else 'passed'
    issues = []
    if failure_status:
        failure_reason = original_execution_status if original_execution_status in {'poll_timeout', 'timeout', 'failed', 'cancelled', 'cleanup_failed'} else 'poll_timeout'
        issues.append({'failure': f'execution_status={failure_reason}'})
    phase1_input = raw.get('phase1_acceptance')
    # Completed runs always go through the strict phase-one gates.  This is
    # intentionally unconditional so legacy payloads cannot bypass them.
    if execution_status == 'completed' or phase1_input is not None or raw.get('delivery_before') is not None or raw.get('delivery_after') is not None:
        from .phase1_acceptance import assess_case_acceptance
        submission = dict(raw.get('submission') or raw.get('payload') or {})
        submission.setdefault('task_id', raw.get('task_id') or task.get('id') or task.get('task_id'))
        acceptance = assess_case_acceptance(
            submission=submission,
            lifecycle={'status': lifecycle_status or execution_status},
            delivery_before=raw.get('delivery_before'), delivery_after=raw.get('delivery_after'),
            phase_integrity=raw.get('phase_integrity'), business_output=raw.get('business_output'),
        )
        if isinstance(phase1_input, dict):
            direct_status = phase1_input.get('status')
            if direct_status in {'passed', 'failed', 'blocked', 'inconclusive'}:
                test_status = direct_status
                if phase1_input.get('failure'):
                    issues.append({'failure': phase1_input['failure']})
            else:
                test_status = acceptance['status']
        else:
            test_status = acceptance['status']

    # Execution failures are authoritative: acceptance cannot report a failed
    # run as passed, even when phase-one evidence was explicitly marked passed.
    if failure_status:
        test_status = 'failed'

    evidence_status = 'complete' if nodes['items'] and diagnostics and videos else 'partial' if nodes['items'] or diagnostics or videos else 'missing'
    return ChainEvidence(
        chain_id=raw.get('chain_id', 'unknown'), execution_mode=raw.get('execution_mode', 'unknown'), scene_type=raw.get('scene_type'),
        submission=raw.get('submission') or raw.get('payload') or {},
        execution=ExecutionState(status=execution_status if execution_status in {'not_started','created','running','completed','failed','timeout','cancelled'} else 'not_started', task_id=raw.get('task_id') or task.get('id'), started_at=raw.get('started_at'), finished_at=raw.get('finished_at'), error=raw.get('execution_error') or raw.get('blocked_reason')),
        polling=raw.get('poll_events') or [], stages=raw.get('phases') or [],
        evidence=EvidenceState(status=evidence_status, nodes=nodes['items'], node_source=nodes['source'], diagnostics=diagnostics, screenshots=raw.get('screenshots') or [], videos=videos),
        delivery_safety=DeliverySafety(**delivery), cleanup=raw.get('cleanup') or {},
        verdict=ChainVerdict(execution_status=execution_status, test_status=test_status, evidence_status=evidence_status, business_verdict='not_applicable' if test_status == 'blocked' else 'fail' if test_status == 'failed' else 'insufficient_evidence' if evidence_status != 'complete' or delivery['verdict'] == 'insufficient_evidence' else 'pass', issues=issues),
    )

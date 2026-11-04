"""Read-only six-chain daily full-suite planner and report."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8')) if not isinstance(path, dict) else deepcopy(path)


def _status(chain, supplied, not_applicable):
    if chain['chain_id'] in not_applicable:
        return 'not_applicable', 'EXPLICIT_NOT_APPLICABLE'
    if not supplied:
        return 'blocked', 'SOURCE_INPUT_MISSING'
    if 'ip_evidence' in chain.get('preconditions', []):
        assets = supplied if isinstance(supplied, list) else [supplied]
        if len(assets) < 2 or not any(a.get('ip_evidence') or a.get('ip_name') for a in assets if isinstance(a, dict)):
            return 'blocked', 'IP_PRECONDITION_UNSATISFIED'
    return 'planned', None


def build_full_plan(config_path, input_data=None):
    config = _load(config_path)
    data = deepcopy(input_data or {})
    profiles = data.get('feature_profiles') or config.get('default_feature_profiles', ['F0'])
    sources = data.get('sources') or {}
    rotation_index = int(data.get('source_rotation_index', 0))
    cases = []
    for chain in config['chains']:
        rotation = chain.get('source_rotation') or config.get('rotation', [])
        source_type = rotation[rotation_index % len(rotation)] if rotation else None
        supplied = sources.get(source_type) if source_type else None
        status, reason = _status(chain, supplied, set(data.get('not_applicable', [])))
        case_id = hashlib.sha1(f"{data.get('date','today')}:{chain['chain_id']}".encode()).hexdigest()[:12]
        cases.append({
            'suite_case_id': case_id, 'chain_id': chain['chain_id'], 'template_id': chain['template_id'],
            'feature_profile_id': profiles[0] if len(profiles) == 1 else profiles,
            'source_type': source_type, 'classification': 'baseline', 'status': status,
            'preconditions': chain.get('preconditions', []), 'not_applicable': status == 'not_applicable',
            'blocked_reason': reason, 'execution_role': chain['execution_role'],
            'material_policy': chain.get('material_policy'), 'old_material_is_dynamic': False,
            'observation': {'source': {'type': source_type, 'refs': []}, 'ingest': {'refs': []}, 'asset': {'refs': []},
                'topic': {'refs': []}, 'scenario': {'refs': []}, 'task': {'refs': []}, 'phases': [],
                'parent_child': {'refs': []}, 'video': {'refs': []}, 'acceptance': {'refs': []}, 'diagnosis': {'refs': []},
                'run_refs': data.get('run_refs', []), 'branch_refs': data.get('branch_refs', [])}
        })
    return {'suite_id': config.get('suite_id'), 'date': data.get('date'), 'plan_only': True,
            'remote_write_performed': False, 'cases': cases,
            'profile_distribution': dict(Counter(str(c['feature_profile_id']) for c in cases)),
            'source_distribution': dict(Counter(c['source_type'] for c in cases)),
            'status': 'planned'}


def full_report(plan):
    cases = plan.get('cases', [])
    counts = Counter(c.get('status') for c in cases)
    active = [c for c in cases if c.get('status') not in ('blocked', 'not_applicable')]
    overall = 'passed' if active and all(c.get('status') == 'passed' for c in active) else 'partial' if any(c.get('status') == 'passed' for c in cases) else 'blocked'
    return {'suite_id': plan.get('suite_id'), 'overall': overall, 'counts': dict(counts),
            'by_chain': {c['chain_id']: c['status'] for c in cases},
            'by_feature_profile': dict(Counter(str(c['feature_profile_id']) for c in cases)),
            'by_source': dict(Counter(c['source_type'] for c in cases)),
            'by_phase': dict(Counter(p for c in cases for p in c.get('observation', {}).get('phases', []))),
            'cases': cases}

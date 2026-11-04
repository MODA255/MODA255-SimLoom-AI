from collections import Counter

_CATEGORY = {
    'TEST_DATA_BLOCKED':'TEST_DATA', 'IP_MATERIAL_INSUFFICIENT':'TEST_DATA', 'IP_EVIDENCE_MISMATCH':'TEST_DATA',
    'ASSET_FAILED':'MATERIAL', 'ASSET_TIMEOUT':'MATERIAL', 'NO_UNIQUE_MATCH':'MATERIAL',
    'TASK_POLL_TIMEOUT':'TASK', 'TASK_PHASE_FAILED':'TASK', 'CHILD_TASK_PARTIAL_FAILURE':'TASK', 'AUTOMATION_SOURCE_NOT_CONFIRMED':'TASK',
    'MEDIA_ACCEPTANCE_FAILED':'MEDIA', 'INDEPENDENT_MEDIA_MISSING':'MEDIA',
    'DELIVERY_SIDE_EFFECT':'SAFETY', 'DELIVERY_CONFIG_NOT_EMPTY':'SAFETY',
    'SOURCE_INPUT_MISSING':'ENVIRONMENT', 'SOURCE_SEARCH_BLOCKED':'ENVIRONMENT', 'ADAPTER_MISSING':'ENVIRONMENT',
}


def _category(reason):
    if reason in _CATEGORY: return _CATEGORY[reason]
    if reason.startswith(('SOURCE_', 'FOLDER_', 'AUTH_')): return 'ENVIRONMENT'
    if reason.startswith(('MEDIA_', 'VIDEO_')): return 'MEDIA'
    if reason.startswith(('TASK_', 'AUTOMATION_')): return 'TASK'
    if reason.startswith(('IP_', 'ASSET_', 'MATERIAL_')): return 'TEST_DATA'
    return 'UNKNOWN'


def analyze_failure(branch):
    branch = branch or {}; reason = str(branch.get('reason') or branch.get('error_type') or 'UNKNOWN')
    category = _category(reason); evidence = {'reason':reason, 'task_ids':[x.get('task_id') for x in branch.get('task_refs',[]) if x.get('task_id')], 'video_ids':(branch.get('task_graph') or {}).get('video_ids',[])}
    gate_reasons = (branch.get('ip_material_gate') or {}).get('reasons', [])
    if reason == 'TEST_DATA_BLOCKED' and gate_reasons:
        root = 'IP_MATERIAL_' + gate_reasons[0]
        category = 'TEST_DATA'; evidence['gate_reasons'] = gate_reasons; action='FIX_INPUT_DATA'
    elif reason == 'TASK_POLL_TIMEOUT' and (branch.get('task_graph') or {}).get('stalled_phases'):
        root='PHASE_STALLED'; evidence['phase']=(branch.get('task_graph') or {}).get('stalled_phases')[0]; action='INSPECT_TASK_LOGS'
    else:
        root=reason; action={'TEST_DATA':'FIX_INPUT_DATA','MATERIAL':'INSPECT_INGEST_LOGS','TASK':'INSPECT_TASK_LOGS','MEDIA':'INSPECT_MEDIA_ARTIFACT','SAFETY':'STOP_AND_REVIEW','ENVIRONMENT':'CHECK_ENVIRONMENT'}.get(category,'MANUAL_REVIEW')
    return {'category':category,'root_cause':root,'next_action':action,'evidence':evidence}


def build_failure_report(plan):
    failures=[]
    for branch_id, branch in (plan or {}).get('branches',{}).items():
        if branch.get('status') in {'failed','waiting','blocked'} and branch.get('reason'):
            failures.append({'branch_id':branch_id, **analyze_failure(branch)})
    return {'run_id':(plan or {}).get('run_id'),'failure_count':len(failures),'by_category':dict(Counter(x['category'] for x in failures)),'failures':failures}

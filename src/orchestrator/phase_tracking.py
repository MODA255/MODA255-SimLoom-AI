def _task_id(task): return (task or {}).get('id') or (task or {}).get('task_id')


def _normalize(task, fetched=None):
    task = dict(task or {}); fetched = fetched or {}
    return {'task_id': _task_id(task), 'status': task.get('status'), 'phases': task.get('phases', fetched.get('phases', [])), 'fetched_phases': fetched.get('phases', []), 'raw': task}


def collect_phase_snapshot(parent, children, phase_fetcher=None):
    if phase_fetcher is None and not (parent or {}).get('phases') and not any(c.get('phases') for c in children or []):
        return {'status':'unavailable','reason':'PHASE_ROUTE_UNCONFIGURED','parent':{},'children':[]}
    def one(task):
        fetched = phase_fetcher(_task_id(task)) if phase_fetcher and _task_id(task) else None
        return _normalize(task, fetched)
    return {'status':'available','parent':one(parent),'children':[one(c) for c in children or []]}


def phase_snapshot_status(snapshot, stalled_names=()):
    stalled=set(stalled_names or []); failed=[]
    for group in ('parent','children'):
        values=[snapshot.get(group)] if group == 'parent' else snapshot.get(group, [])
        for task in values:
            for phase in task.get('phases', []):
                status=str(phase.get('status','')).lower(); name=phase.get('name') or phase.get('phase')
                if status in {'failed','error','cancelled'}: failed.append(name)
                if name in stalled: return {'status':'stalled','phase':name,'failed_phases':failed}
    return {'status':'failed' if failed else 'healthy','failed_phases':failed}

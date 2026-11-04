from .automation_children import collect_automation_children


def run_automation_parent_child(adapter, parent_id, *, child_poller=None):
    tree = collect_automation_children(adapter, parent_id)
    child_results = []
    for child in tree['children']:
        child_id = child.get('child_task_id')
        if not child_id:
            child_results.append({**child, 'execution_status': 'blocked', 'reason': 'CHILD_TASK_ID_MISSING'})
            continue
        if child_poller:
            child_results.append({**child, 'lifecycle': child_poller(child_id)})
        else:
            child_results.append({**child, 'execution_status': 'not_polled'})
    return {**tree, 'child_results': child_results, 'child_count': len(child_results), 'failed_children': sum(1 for x in child_results if (x.get('lifecycle') or {}).get('poll_status') == 'failed' or x.get('execution_status') == 'blocked')}

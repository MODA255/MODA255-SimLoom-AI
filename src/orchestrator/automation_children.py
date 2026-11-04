from orchestrator.automation_adapters import AutomationTaskAdapter


def collect_automation_children(adapter, parent_id):
    stages = adapter.stages(parent_id)
    sessions = stages.get('sessions') or stages.get('items') or []
    children = []
    for session in sessions:
        for segment in session.get('segments') or []:
            child_id = segment.get('child_task_id') or segment.get('task_id')
            children.append({'session_id': session.get('id') or session.get('session_id'), 'segment_id': segment.get('id') or segment.get('segment_id'), 'child_task_id': child_id, 'status': segment.get('status')})
    return {'parent_task_id': parent_id, 'sessions': sessions, 'children': children}

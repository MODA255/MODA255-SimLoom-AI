from orchestrator.automation_children import collect_automation_children


def test_collect_automation_children_flattens_sessions_and_segments():
    class Adapter:
        def stages(self, parent_id):
            return {'sessions': [{'id': 's1', 'segments': [{'id': 'seg1', 'child_task_id': 'child1', 'status': 'completed'}]}]}
    result = collect_automation_children(Adapter(), 'parent1')
    assert result['parent_task_id'] == 'parent1'
    assert result['children'][0]['child_task_id'] == 'child1'

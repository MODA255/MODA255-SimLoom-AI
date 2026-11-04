from orchestrator.automation_lifecycle import run_automation_parent_child


def test_parent_child_lifecycle_polls_each_child():
    calls=[]
    class Adapter:
        def stages(self, parent_id): return {'sessions': [{'id':'s1','segments':[{'id':'seg1','child_task_id':'c1'},{'id':'seg2','child_task_id':'c2'}]}]}
    result=run_automation_parent_child(Adapter(),'p1',child_poller=lambda cid: calls.append(cid) or {'poll_status':'completed'})
    assert calls == ['c1','c2']
    assert result['child_count'] == 2
    assert result['failed_children'] == 0


def test_missing_child_id_is_blocked():
    class Adapter:
        def stages(self,parent_id): return {'sessions':[{'segments':[{'id':'seg1'}]}]}
    result=run_automation_parent_child(Adapter(),'p1')
    assert result['failed_children'] == 1
    assert result['child_results'][0]['reason'] == 'CHILD_TASK_ID_MISSING'

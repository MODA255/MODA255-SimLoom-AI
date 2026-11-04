from orchestrator.automation_execution import execute_automation_children


def test_execute_automation_children_polls_child_tasks():
    calls=[]
    class Adapter:
        def stages(self,p): return {'sessions':[{'id':'s','segments':[{'id':'seg','child_task_id':'c'}]}]}
        def get_daily_task(self,kind,task_id): calls.append(task_id); return {'id':task_id,'status':'completed'}
    result=execute_automation_children(Adapter(),'parent',interval_seconds=0)
    assert calls == ['c']
    assert result['failed_children'] == 0

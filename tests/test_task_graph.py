from orchestrator.task_graph import build_task_graph, classify_task_graph


def test_builds_traceable_parent_session_segment_child_video_graph():
    parent = {'id':'p1','status':'completed','creation_source':'automation_task','sessions':[{'id':'s1','segments':[{'id':'g1','child_tasks':[{'id':'c1','status':'completed','videos':[{'id':'v1'}]}]}]}]}
    graph = build_task_graph(parent)
    assert graph['root_id'] == 'p1'
    assert graph['links'] == [
        {'from':'p1','to':'s1','type':'session'},
        {'from':'s1','to':'g1','type':'segment'},
        {'from':'g1','to':'c1','type':'child_task'},
        {'from':'c1','to':'v1','type':'video'},
    ]
    assert graph['video_ids'] == ['v1']


def test_classifies_partial_success_when_one_child_fails():
    parent = {'id':'p1','status':'completed','creation_source':'automation_task','children':[{'id':'c1','status':'completed','media_paths':['v1']},{'id':'c2','status':'failed'}]}
    result = classify_task_graph(parent, parent['children'])
    assert result['status'] == 'partial_success'
    assert result['failed_child_task_ids'] == ['c2']
    assert result['successful_child_task_ids'] == ['c1']


def test_records_phase_duration_and_stalled_phase():
    parent = {'id':'p1','status':'processing','phases':[{'name':'record','status':'completed','started_at':'2026-10-19T10:00:00Z','finished_at':'2026-10-19T10:01:30Z'},{'name':'cut','status':'processing','started_at':'2026-10-19T10:01:30Z'}]}
    graph = build_task_graph(parent, now='2026-10-19T10:05:30Z')
    assert graph['phase_metrics'][0]['duration_seconds'] == 90
    assert graph['stalled_phases'] == ['cut']

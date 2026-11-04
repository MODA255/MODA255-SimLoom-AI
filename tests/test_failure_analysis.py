from orchestrator.failure_analysis import analyze_failure, build_failure_report


def test_classifies_material_failure_before_task_creation():
    result = analyze_failure({'status':'blocked','reason':'TEST_DATA_BLOCKED','ip_material_gate':{'reasons':['ASR_MISSING']}})
    assert result['category'] == 'TEST_DATA'
    assert result['root_cause'] == 'IP_MATERIAL_ASR_MISSING'
    assert result['next_action'] == 'FIX_INPUT_DATA'


def test_classifies_stalled_phase_with_timeline_evidence():
    result = analyze_failure({'status':'waiting','reason':'TASK_POLL_TIMEOUT','task_graph':{'stalled_phases':['cut'], 'phase_metrics':[{'name':'cut','duration_seconds':3600}]}})
    assert result['category'] == 'TASK'
    assert result['root_cause'] == 'PHASE_STALLED'
    assert result['evidence']['phase'] == 'cut'
    assert result['next_action'] == 'INSPECT_TASK_LOGS'


def test_classifies_media_failure_and_preserves_ids():
    result = analyze_failure({'status':'failed','reason':'MEDIA_ACCEPTANCE_FAILED','task_refs':[{'task_id':'t1'}], 'task_graph':{'video_ids':['v1']}})
    assert result['category'] == 'MEDIA'
    assert result['root_cause'] == 'MEDIA_ACCEPTANCE_FAILED'
    assert result['evidence']['task_ids'] == ['t1']
    assert result['evidence']['video_ids'] == ['v1']


def test_builds_daily_failure_summary():
    report = build_failure_report({'run_id':'r1','branches':{'a':{'status':'failed','reason':'TASK_PHASE_FAILED'}, 'b':{'status':'passed'}}})
    assert report['failure_count'] == 1
    assert report['by_category']['TASK'] == 1
    assert report['failures'][0]['branch_id'] == 'a'

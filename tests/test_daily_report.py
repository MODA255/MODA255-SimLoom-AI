from orchestrator.daily_report import aggregate_daily_report


def test_aggregates_chain_status_and_root_causes():
    report = aggregate_daily_report({'run_id':'r1','result':'partial_success','branches':{
        'auto_ecom': {'status':'passed','task_graph':{'phase_metrics':[{'name':'record','duration_seconds':60}]}},
        'auto_theme': {'status':'failed','failure_analysis':{'category':'TASK','root_cause':'PHASE_STALLED','next_action':'INSPECT_TASK_LOGS'},'task_graph':{'phase_metrics':[{'name':'cut','duration_seconds':3600}]}},
        'manual_ip': {'status':'blocked','failure_analysis':{'category':'TEST_DATA','root_cause':'IP_MATERIAL_ASR_MISSING','next_action':'FIX_INPUT_DATA'}}
    }})
    assert report['overall'] == 'partial_success'
    assert report['status_counts'] == {'passed':1,'failed':1,'blocked':1}
    assert report['root_cause_counts'] == {'PHASE_STALLED':1,'IP_MATERIAL_ASR_MISSING':1}
    assert report['slowest_phase']['name'] == 'cut'


def test_empty_run_is_blocked_without_fake_success():
    report = aggregate_daily_report({'run_id':'r2','result':'blocked','branches':{}})
    assert report['overall'] == 'blocked'
    assert report['failure_count'] == 0

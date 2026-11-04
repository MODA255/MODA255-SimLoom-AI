from orchestrator.live_source_payload import select_and_apply_source


def test_selected_source_replaces_all_live_urls_without_mutating_input():
    payload={'source_urls':['bad'],'ingest_config_snapshot':{'live_urls':['bad']}}
    result=select_and_apply_source(payload,{'selected_url':'good'})
    assert result['source_urls']==['good']
    assert result['ingest_config_snapshot']['live_urls']==['good']
    assert payload['source_urls']==['bad']

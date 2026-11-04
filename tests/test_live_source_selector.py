from orchestrator.live_source_selector import choose_live_source


def test_selector_uses_search_when_pool_empty():
    result=choose_live_source([],search=lambda:['u1','u2'],probe=lambda urls:{'status':'passed','selected_url':urls[1],'attempts':[]})
    assert result['selected_url']=='u2'
    assert result['discovery_count']==2


def test_selector_limits_attempts():
    seen=[]
    result=choose_live_source(['1','2','3'],max_attempts=2,probe=lambda urls:seen.extend(urls) or {'status':'failed','attempts':[]})
    assert seen==['1','2']
    assert result['max_attempts']==2

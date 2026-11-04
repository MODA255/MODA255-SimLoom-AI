from orchestrator.live_source_search_adapter import search_live_candidates


def test_search_adapter_returns_urls_and_evidence():
    class Skill:
        def run(self, request):
            class Result:
                results=[type('R',(),{'url':'https://live.test/1'})()]
                def model_dump(self): return {'status':'success','results':[{'url':'https://live.test/1'}]}
            return Result()
    urls,evidence=search_live_candidates(Skill(),'主题')
    assert urls==['https://live.test/1']
    assert evidence['status']=='success'

import json
from orchestrator.agent_bridge import search_via_agent_bridge


def test_agent_bridge_converts_candidates():
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'status':'success','candidates':[{'url':'https://live.test/1'}]}
    class Client:
        @staticmethod
        def post(*args,**kwargs): return Response()
    result=search_via_agent_bridge('https://bridge.test', 'douyin live', client=Client)
    assert result['candidates'][0]['url']=='https://live.test/1'


def test_agent_bridge_requires_url():
    try: search_via_agent_bridge('', 'q')
    except Exception as exc: assert 'AGENT_BRIDGE_URL_MISSING' in str(exc)
    else: raise AssertionError('expected missing bridge url')

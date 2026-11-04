from orchestrator.live_source_probe import probe_live_candidates


def test_probe_falls_back_to_second_candidate():
    class Response:
        def __init__(self,url,text,status=200): self.url=url;self.text=text;self.status_code=status;self.headers={'content-type':'text/html'}
        @property
        def is_success(self): return self.status_code < 400
    class Client:
        @staticmethod
        def get(url,**kwargs): return Response(url,'stream_url="https://stream.example/live.m3u8"' if 'good' in url else 'no stream')
    result=probe_live_candidates(['https://bad','https://good'],client=Client)
    assert result['status']=='passed'
    assert result['selected_url']=='https://good'
    assert len(result['attempts'])==2


def test_probe_records_required_diagnostics():
    class Response:
        url='https://final';text='no stream';status_code=200;headers={'content-type':'text/html'}
        @property
        def is_success(self): return True
    class Client:
        @staticmethod
        def get(url,**kwargs): return Response()
    result=probe_live_candidates(['https://source'],client=Client)
    attempt=result['attempts'][0]
    assert attempt['http_status']==200
    assert attempt['final_url']=='https://final'
    assert attempt['has_stream_url'] is False
    assert attempt['failure_stage']=='stream_resolution'

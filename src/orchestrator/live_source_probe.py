from urllib.parse import urlparse
import httpx


def probe_live_url(url, *, client=httpx, timeout=20, headers=None):
    result={'url':url,'http_status':None,'final_url':None,'live_status':'unknown','has_stream_url':False,'stream_url':None,'failure_stage':None,'raw_fields':{},'requires_login':False,'error':None}
    try:
        response=client.get(url,follow_redirects=True,timeout=timeout,headers=headers or {})
        result['http_status']=response.status_code;result['final_url']=str(response.url)
        text=response.text[:200000]
        result['raw_fields']={'content_type':response.headers.get('content-type'),'content_length':response.headers.get('content-length'),'has_live_marker':'live' in text.lower()}
        result['live_status']='page_ok' if response.is_success else 'http_error'
        result['requires_login']=response.status_code in {401,403} or 'login' in text.lower()[:5000]
        for marker in ('stream_url','hls_url','flv_url','m3u8'):
            if marker in text.lower(): result['has_stream_url']=True;result['failure_stage']=None;break
        if not result['has_stream_url']: result['failure_stage']='stream_resolution'
    except Exception as exc:
        result['failure_stage']='page_fetch';result['error']=f'{type(exc).__name__}: {exc}'
    return result


def probe_live_candidates(urls, **kwargs):
    attempts=[probe_live_url(url,**kwargs) for url in urls]
    selected=next((x for x in attempts if x['http_status'] and x['http_status'] < 400 and x['has_stream_url']),None)
    return {'status':'passed' if selected else 'failed','selected_url':selected['url'] if selected else None,'attempts':attempts,'failure_code':None if selected else 'LIVE_STREAM_RESOLUTION_FAILED'}

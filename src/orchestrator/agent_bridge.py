from urllib.parse import urljoin
import httpx


class AgentBridgeError(RuntimeError): pass


def search_via_agent_bridge(base_url, query, token=None, *, client=httpx, limit=10):
    if not base_url: raise AgentBridgeError('AGENT_BRIDGE_URL_MISSING')
    headers={'Accept':'application/json'}
    if token: headers['Authorization']=f'Bearer {token}'
    response=client.post(urljoin(base_url.rstrip('/')+'/', 'source-search'), json={'query':query,'platform':'douyin','content_type':'live','limit':limit}, headers=headers, timeout=60)
    response.raise_for_status(); body=response.json()
    candidates=body.get('candidates') or body.get('results') or []
    return {'status':body.get('status','success'),'candidates':candidates,'raw':body}

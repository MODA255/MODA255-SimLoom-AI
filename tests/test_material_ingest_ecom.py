import pytest
from orchestrator.material_ingest import ingest_and_wait_ready, MaterialIngestError
from orchestrator.ecom_payload import build_ecom_payload

class FakeAdapter:
    def __init__(self, assets, progress):
        self.assets = assets
        self.progress = list(progress)
        self.imports = []
    def ingest_url(self, payload):
        self.imports.append(payload)
        return {"status": "queued"}
    def list_assets(self, folder_id): return {"items": self.assets}
    def get_asset(self, asset_id): return next(a for a in self.assets if a.get("id") == asset_id)
    def get_asset_progress(self, asset_id): return self.progress.pop(0) if self.progress else {"status":"ready"}

def test_accepted_without_id_backfills_and_waits_ready():
    a = {"id":"asset-1", "title":"Demo", "source_url":"https://x/a", "status":"downloading"}
    out = ingest_and_wait_ready(FakeAdapter([a], [{"status":"processing"},{"status":"ready"}]), {"url":"https://x/a","title":"Demo"}, "folder", "https://x/a", "Demo", timeout=1, poll=0)
    assert out["asset"]["asset_id"] == "asset-1"
    assert out["status"] == "ready"
    assert out["evidence"]

def test_unrelated_asset_is_not_matched():
    with pytest.raises(MaterialIngestError, match="NO_UNIQUE_MATCH"):
        ingest_and_wait_ready(FakeAdapter([{"id":"x", "title":"Other", "source_url":"https://other"}], []), {"url":"https://x/a"}, "folder", "https://x/a", "Demo", timeout=0, poll=0)

def test_failed_and_timeout():
    a={"id":"a","title":"Demo","source_url":"u","status":"downloading"}
    with pytest.raises(MaterialIngestError, match="ASSET_FAILED"):
        ingest_and_wait_ready(FakeAdapter([a],[{"status":"failed","error_msg":"bad"}]), {"url":"u"}, "f", "u", "Demo", timeout=1, poll=0)
    with pytest.raises(MaterialIngestError, match="ASSET_TIMEOUT"):
        ingest_and_wait_ready(FakeAdapter([a],[{"status":"processing"}]), {"url":"u"}, "f", "u", "Demo", timeout=0, poll=0)

def test_ecom_payload_disables_delivery_and_preserves_business_product():
    out = build_ecom_payload({"asset_id":"a1","status":"ready"}, {"topic":"summer"}, {"recipe":"wrong","product_id":"biz-1","delivery_enabled":True,"advertiser":"bad","products":["bad"]})
    assert out["recipe"] == "ecom_remix"
    assert out["num_versions"] == 1
    assert out["delivery_enabled"] is False
    assert out["advertiser"] == [] and out["awemes"] == [] and out["products"] == []
    assert out["promotion_goal"] == "" and out["adlab_scene"] == ""
    assert out["business"]["product_id"] == "biz-1"
    assert out["assets"][0]["asset_id"] == "a1"

def test_duplicate_exact_matches_are_blocked():
    rows=[{'id':str(i),'title':'Demo','source_url':'u'} for i in range(2)]
    with pytest.raises(MaterialIngestError, match='AMBIGUOUS'):
        ingest_and_wait_ready(FakeAdapter(rows,[]),{'url':'u'},'f','u','Demo',timeout=0,poll=0)

def test_title_only_allowed_when_url_missing_but_conflicting_url_rejected():
    a={'id':'a','title':'Demo','status':'active'}
    result=ingest_and_wait_ready(FakeAdapter([a],[{}]),{'url':'u'},'f','u','Demo',timeout=0,poll=0)
    assert result['asset']['asset_id']=='a'
    with pytest.raises(MaterialIngestError):
        ingest_and_wait_ready(FakeAdapter([{**a,'source_url':'other'}],[]),{'url':'u'},'f','u','Demo',timeout=0,poll=0)

def test_mapping_candidate_and_dynamic_cannot_override_fixed():
    out=build_ecom_payload({'id':'a','status':'active'}, {'topic':'summer','enable_subtitle':False,'recipe':'evil'}, {'subtitle_enabled':True,'product_id':'business-p','num_versions':2})
    assert out['candidate']['master_asset_ids']==['a']
    assert out['candidate']['query']=='summer'
    assert out['candidate']['enable_subtitle'] is True
    assert out['candidate']['num_versions']==2
    assert out['candidate']['auto_delivery_enabled'] is False
    assert out['business']['product_id']=='business-p'
    assert 'business' not in out['candidate'] and 'assets' not in out['candidate']
    assert out['status']=='blocked' and out['validation']

def test_not_ready_is_blocked():
    out=build_ecom_payload({'asset_id':'a','status':'processing'}, {'topic':'x'}, {})
    assert out['status']=='blocked'


def test_adapter_paths_refresh_and_unknown_routes(monkeypatch):
    import httpx
    from orchestrator.adapters import RemoteAdapter, RemoteRouteError
    calls=[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        req=httpx.Request(method,url)
        if url.endswith('/auth/refresh'):
            return httpx.Response(200,json={'access_token':'NEW_SECRET'},request=req)
        return httpx.Response(401 if len(calls)==1 else 200,json={'status':'active'},request=req)
    monkeypatch.setattr(httpx,'request',request)
    monkeypatch.setenv('MATRIXGEN_REFRESH_TOKEN','REFRESH_SECRET')
    a=RemoteAdapter({'target':{'base_url':'https://example.test'},'remote_api':{'routes':{'asset_detail':'GET /assets/{asset_id}','bad':'bogus'}}})
    a.get_asset('asset-1')
    assert calls[0][1]=='https://example.test/api/v1/assets/asset-1'
    assert calls[-1][2]['headers']['Authorization']=='Bearer NEW_SECRET'
    assert calls[-1][2]['params']=={}
    with pytest.raises(RemoteRouteError): a.request('missing')
    with pytest.raises(RemoteRouteError): a.request('bad')


def test_dotenv_credentials_are_private(tmp_path,monkeypatch):
    from orchestrator.config import load_config
    from orchestrator.adapters import RemoteAdapter
    import json
    monkeypatch.delenv('MATRIXGEN_TOKEN',raising=False)
    (tmp_path/'.env').write_text('MATRIXGEN_TOKEN=PRIVATE_TOKEN\n')
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'mode':'remote_live','state_db':':memory:','profiles':{'publish':False}}))
    c=load_config(cfg)
    assert RemoteAdapter(c).token=='PRIVATE_TOKEN'
    assert 'PRIVATE_TOKEN' not in json.dumps(c.snapshot())


def test_idempotent_operation_store_never_resubmits():
    a={'id':'a','title':'Demo','status':'active','source_url':'u'}
    fake=FakeAdapter([a],[{}]); store={}
    first=ingest_and_wait_ready(fake,{'url':'u'},'f','u','Demo',timeout=0,poll=0,operation_key='key',operation_store=store)
    second=ingest_and_wait_ready(fake,{'url':'u'},'f','u','Demo',timeout=0,poll=0,operation_key='key',operation_store=store)
    assert len(fake.imports)==1 and second['asset']==first['asset']


def test_all_adapter_methods_use_httpx_without_network(monkeypatch):
    import httpx
    from orchestrator.adapters import RemoteAdapter
    import json
    from pathlib import Path
    cfg=json.loads((Path(__file__).parents[1]/'configs/remote-test.local.json').read_text())
    calls=[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        return httpx.Response(200,json={'ok':True},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx,'request',request)
    a=RemoteAdapter(cfg)
    a.ingest_url({'url':'u'}); a.list_assets('f'); a.get_asset('a'); a.get_asset_progress('a'); a.get_asset_progress_phases('a')
    a.create_vfc_task({'recipe':'ecom_remix'}); a.get_task('t'); a.get_phases('t'); a.get_videos('t'); a.download('t','v')
    assert len(calls)==10
    assert calls[0][0]=='POST' and calls[5][0]=='POST'
    assert calls[-1][1].endswith('/vfc/tasks/t/videos/v/download')
    assert calls[1][2]['params']=={'folder_id':'f'}
    assert all('{' not in url for _,url,_ in calls)

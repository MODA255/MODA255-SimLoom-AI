import json
from pathlib import Path
import pytest
from orchestrator.config import load_config
from orchestrator.repository import Repository
from orchestrator.engine import Orchestrator

ROOT = Path(__file__).parents[1]
def cfg(tmp_path, mode='simulation'):
    d=json.loads((ROOT/'configs/example.json').read_text())
    d.update({'mode':mode,'state_db':str(tmp_path/'state.db')})
    d['required_sources']=['link']
    d['profiles'].update({'folder_id':'folder-1','template_version':'v1','fixed_config_version':'fixed-v1','publish':False})
    d['source_policy']={'allowed_platforms':['douyin','weibo'],'allowed_url_types':['direct_video','verified_ingest_platform','live_url']}
    d['scenario']={'topic':'fixed topic','template':'fixed-template','ecom_mode':'manual'}
    return load_config(d)

def test_daily_plan_dynamic_missing_input_blocked(tmp_path):
    o=Orchestrator(cfg(tmp_path),Repository(str(tmp_path/'state.db')))
    out=o.daily_plan({'branch':'link','mode':'dynamic'})
    assert out['status']=='blocked'
    assert out['reason']=='SOURCE_INPUT_MISSING'

def test_manual_vfc_cannot_be_automation(tmp_path):
    o=Orchestrator(cfg(tmp_path),Repository(str(tmp_path/'state.db')))
    out=o.daily_plan({'branch':'link','mode':'manual_ecom','asset':{'asset_id':'a','status':'ready','source_type':'link','source_url':'https://v.douyin.com/x'}})
    assert out['status'] in ('blocked','planned')
    assert out.get('automation_source') != 'manual_vfc'

def test_simulation_plan_saves_refs_and_is_not_successful_run(tmp_path):
    o=Orchestrator(cfg(tmp_path),Repository(str(tmp_path/'state.db')))
    out=o.daily_plan({'branch':'link','mode':'fixed','asset':{'asset_id':'a','status':'ready','source_type':'link','source_url':'https://v.douyin.com/x'}})
    assert out['plan_only'] is True
    run=o.daily_run(out['run_id'])
    assert run['status'] in ('planned','blocked')
    assert run['result'] != 'passed'

def test_remote_requires_execute_and_reprints_plan(tmp_path):
    o=Orchestrator(cfg(tmp_path,'remote_live'),Repository(str(tmp_path/'state.db')))
    out=o.daily_run({'branch':'link','mode':'fixed','asset':{'asset_id':'a','status':'ready','source_type':'link','source_url':'https://v.douyin.com/x'}})
    assert out['status']=='blocked'
    assert out['reason']=='EXECUTE_REQUIRED'
    assert out['safety_plan']

from orchestrator.adapters import RemoteAdapter
from orchestrator.daily import DailyRunner
from orchestrator.automation import verify_automation_result

ASSET={'asset_id':'a','status':'active','source_type':'link','source_url':'https://v.douyin.com/x'}
def auto_spec(mode='auto_ecom'):
    kinds={'auto_ecom':'ecom_mix','auto_theme':'theme_discovery','auto_ip':'ip_promo'}
    return {'asset':dict(ASSET),'topic':{'topic':'facts'},'task_mode':mode,'classification':'baseline','parameters_confirmed':True,'automation_payload':{'kind':kinds[mode],'source_url':ASSET['source_url'],'auto_delivery_enabled':False}}

class RemoteFake:
    def __init__(self, source='automation_task'): self.creates=[]; self.source=source
    def create_daily_task(self,kind,payload,execute=False):
        assert execute; self.creates.append((kind,payload)); return {'id':'p'}
    def get_daily_task(self,kind,tid): return {'id':tid,'status':'completed','creation_source':'automation_task','children':[{'id':'child'}]}
    def get_task(self,tid): return {'id':tid,'status':'completed','parent_task_id':'p','creation_source':self.source,'media_paths':['fixture.mp4']}

def runner(tmp_path, adapter=None, mode='remote_live'):
    c=cfg(tmp_path,mode); o=Orchestrator(c,Repository(c.state_db))
    return DailyRunner(o,adapter=adapter,media_checker=lambda p:{'valid':True})

@pytest.mark.parametrize('kind,path',[('ecom_mix','/automation/tasks'),('theme_discovery','/automation/tasks'),('ip_promo','/ip-promo-automations')])
def test_confirmed_automation_routes_require_execute(tmp_path,monkeypatch,kind,path):
    import httpx
    calls=[]
    def request(method,url,**kw):
        calls.append((method,url,kw)); return httpx.Response(200,json={'id':'p'},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx,'request',request)
    adapter=RemoteAdapter({'mode':'remote_live','target':{'base_url':'https://example.test'}})
    with pytest.raises(RuntimeError,match='EXECUTE_REQUIRED'): adapter.create_daily_task(kind,{},execute=False)
    assert calls==[]
    adapter.create_daily_task(kind,{},execute=True)
    assert calls[0][0]=='POST' and calls[0][1].endswith(path)

def test_complete_simulation_plan_no_remote_writes(tmp_path):
    f=RemoteFake(); r=runner(tmp_path,f,'simulation')
    plan=r.plan({'branches':{'link':auto_spec()}})
    assert plan['status']=='planned' and plan['scenarios'][0]['scenario_id']
    assert plan['safety_plan']['operations'][0]['endpoint']=='/automation/tasks'
    out=r.run(plan['run_id'],execute=True)
    assert not f.creates and out['result']=='planned'

def test_automation_success_partial_failure_and_resume(tmp_path):
    f=RemoteFake(); r=runner(tmp_path,f)
    plan=r.plan({'branches':{'link':auto_spec(),'live':{'mode':'dynamic'}}})
    out=r.run(plan['run_id'],execute=True)
    assert out['result']=='partial_success'
    assert out['branches']['link']['status']=='passed'
    assert out['branches']['live']['reason']=='SOURCE_INPUT_MISSING'
    assert out['task_refs'][0]['scenario_id']
    out=r.run(out['run_id'],execute=True)
    assert len(f.creates)==1
    assert r.report(out['run_id'])['baseline']['link']['status']=='passed'

def test_manual_child_is_mismatch_not_automation_pass(tmp_path):
    r=runner(tmp_path,RemoteFake('manual_vfc'))
    out=r.run({'branches':{'link':auto_spec()}},execute=True)
    assert out['result']=='failed'
    assert out['branches']['link']['reason']=='AUTOMATION_SOURCE_NOT_CONFIRMED'

def test_delivery_closed_before_and_after_submission(tmp_path):
    f=RemoteFake(); r=runner(tmp_path,f)
    spec=auto_spec(); spec['automation_payload']['advertiser']=['bad']
    assert r.plan({'branches':{'link':spec}})['result']=='blocked'
    assert not f.creates
    f.get_daily_task=lambda kind,tid:{'id':tid,'status':'completed','creation_source':'automation_task','delivery_enabled':True}
    assert r.run({'branches':{'link':auto_spec()}},execute=True)['result']=='failed'

def test_default_run_never_simulates_pass(tmp_path):
    o=Orchestrator(cfg(tmp_path),Repository(str(tmp_path/'state.db')))
    with pytest.raises(TypeError): o.run(simulated_failures={'live':'fake'})

def test_resume_cancelled_never_creates(tmp_path):
    f=RemoteFake(); r=runner(tmp_path,f)
    p=r.plan({'branches':{'link':auto_spec()}})
    r.engine.cancel(p['run_id'])
    assert r.run(p['run_id'],execute=True)['status']=='cancelled'
    assert not f.creates

def test_input_mode_unknown_and_no_source_cannot_be_planned(tmp_path):
    r=runner(tmp_path,mode='simulation')
    spec=auto_spec(); spec['automation_payload'].pop('source_url')
    p=r.plan({'branches':{'link':spec}})
    assert p['status']=='blocked'
    assert p['branches']['link']['reason']=='AUTOMATION_SOURCE_PARAMETERS_MISSING'

def test_automation_parent_manual_cannot_pass(tmp_path):
    f=RemoteFake(); f.get_daily_task=lambda kind,tid:{'id':tid,'status':'completed','creation_source':'manual_vfc','children':[{'id':'child'}]}
    assert runner(tmp_path,f).run({'branches':{'link':auto_spec()}},execute=True)['result']=='failed'

def test_engine_resume_preserves_run_id_and_report(tmp_path):
    o=Orchestrator(cfg(tmp_path),Repository(str(tmp_path/'state.db')))
    p=o.daily_plan({'branches':{'link':auto_spec()}})
    out=o.daily_run(p['run_id'])
    assert out['run_id']==p['run_id']
    assert o.daily_report(out['run_id'])['baseline']['link']['task_mode']=='auto_ecom'

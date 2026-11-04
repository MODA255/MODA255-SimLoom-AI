from orchestrator.remote_materials import select_ip_assets
from orchestrator.cleanup import asset_cleanup_decision


def asset(i, ip='IP-A', status='active', duration=20, clips=4):
    return {'id':f'a{i}','ip_name':ip,'status':status,'duration':duration,'clip_count':clips,'asr':{'text':'x'},'ip_evidence':{'matched':True},'tags':[f'ip_name={ip}']}


def test_select_ip_assets_applies_remote_context_and_gate():
    class Adapter:
        def list_assets(self, folder_id): return {'items':[asset(i) for i in range(9)] + [asset(99, ip='IP-B')]}
    result=select_ip_assets(Adapter(), 'f', 'IP-A', automated=True)
    assert result['status']=='ready'
    assert len(result['assets']) == 9
    assert all(a['ip_name']=='IP-A' for a in result['assets'])
    assert result['gate']['valid'] is True


def test_baseline_asset_is_never_cleanup_candidate():
    result=asset_cleanup_decision({'asset_id':'a1','folder_role':'baseline','referenced_task_ids':[],'age_days':100})
    assert result['status']=='blocked'
    assert result['reason']=='BASELINE_PROTECTED'


def test_referenced_dynamic_asset_is_retained():
    result=asset_cleanup_decision({'asset_id':'a2','folder_role':'dynamic','referenced_task_ids':['t1'],'age_days':100})
    assert result['status']=='blocked'
    assert result['reason']=='ASSET_STILL_REFERENCED'


def test_old_unreferenced_dynamic_asset_is_candidate_only():
    result=asset_cleanup_decision({'asset_id':'a3','folder_role':'dynamic','referenced_task_ids':[],'age_days':31,'retention_days':30})
    assert result['status']=='candidate'
    assert result['delete_remote'] is False

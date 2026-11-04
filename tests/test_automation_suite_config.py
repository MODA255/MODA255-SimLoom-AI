from orchestrator.automation_suite_config import validate_automation_payload


def test_auto_ecom_requires_snapshots_and_delivery_closed():
    r=validate_automation_payload('auto_ecom', {'name':'x','scene_type':'ecom_mix'})
    assert r['status']=='blocked'
    assert 'delivery_enabled' in ''.join(r['missing_fields'])


def test_auto_ip_requires_delivery_inside_mix_snapshot():
    r=validate_automation_payload('auto_ip', {'name':'x','brief':'b','topic':'t','num_scripts':1,'auto_confirm_scripts':True,'material_asset_ids':['a'],'mix_config_snapshot':{'ip_name':'x'}})
    assert r['status']=='blocked'


def test_complete_auto_theme_payload_passes():
    p={'name':'x','scene_type':'theme_discovery','source_platform':'douyin','source_urls':['u'],'asset_folder_id':'f','ingest_config_snapshot':{},'mix_config_snapshot':{},'delivery_enabled':False}
    assert validate_automation_payload('auto_theme',p)['status']=='passed'

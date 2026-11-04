from pathlib import Path
import json

REQUIRED = {
 'auto_ecom': ('name','scene_type','source_platform','source_urls','asset_folder_id','ingest_config_snapshot','mix_config_snapshot','delivery_enabled'),
 'auto_theme': ('name','scene_type','source_platform','source_urls','asset_folder_id','ingest_config_snapshot','mix_config_snapshot','delivery_enabled'),
 'auto_ip': ('name','brief','topic','num_scripts','auto_confirm_scripts','material_asset_ids','mix_config_snapshot'),
}


def validate_automation_payload(chain_id, payload):
    payload=payload or {}; missing=[k for k in REQUIRED.get(chain_id, ()) if k not in payload]
    delivery = payload.get('delivery_enabled') if chain_id != 'auto_ip' else (payload.get('mix_config_snapshot') or {}).get('delivery_enabled')
    if delivery is not False: missing.append('delivery_enabled=false')
    return {'status':'passed' if not missing else 'blocked','missing_fields':missing,'chain_id':chain_id}


def load_automation_suite(path):
    suite=json.loads(Path(path).read_text(encoding='utf-8'))
    for chain in suite.get('chains',[]):
        check=validate_automation_payload(chain.get('chain_id'),chain.get('payload'));chain['preflight']=check
        if check['status']!='passed': chain['blocked_reason']='AUTOMATION_PAYLOAD_INVALID'
    return suite

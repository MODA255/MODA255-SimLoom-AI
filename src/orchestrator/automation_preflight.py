from .preflight import run_preflight
from .auto_ip_gate import automation_preflight
from .live_source_selector import choose_live_source

AUTOMATION_REQUIRED = ('name', 'scene_type', 'delivery_enabled')


def run_automation_preflight(chain, *, capability_probe=None):
    if chain.get('chain_id') == 'auto_ip':
        ip_check = automation_preflight(chain)
        if ip_check and ip_check['status'] != 'passed':
            return {'status':'blocked','checks':[],'missing_fields':ip_check['missing_fields'],'blocked_reason':'AUTOMATION_PAYLOAD_MISSING_FIELDS','auto_ip_check':ip_check}
        return {'status':'passed','checks':[{'name':'auto_ip_payload','status':'passed'}],'missing_fields':[],'blocked_reason':None,'auto_ip_check':ip_check}
    result = run_preflight(chain, capability_probe=capability_probe)
    payload = chain.get('payload') or {}
    missing = [key for key in AUTOMATION_REQUIRED if key not in payload]
    if missing:
        result['status'] = 'blocked'; result['missing_fields'] = sorted(set(result.get('missing_fields', []) + missing)); result['blocked_reason'] = 'AUTOMATION_PAYLOAD_MISSING_FIELDS'
    if payload.get('delivery_enabled') is not False and not missing:
        result['status'] = 'blocked'; result['blocked_reason'] = 'AUTOMATION_DELIVERY_NOT_CLOSED'
    if 'source_urls' in payload:
        live_probe = choose_live_source(payload.get('source_urls') or [], search=chain.get('source_search'))
        result['live_source_probe'] = live_probe
        if live_probe['status'] != 'passed':
            result['status'] = 'blocked'; result['blocked_reason'] = 'LIVE_SOURCE_UNAVAILABLE'
    return result

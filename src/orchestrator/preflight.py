from .failure_attribution import attribute_failure

REQUIRED_BY_MODE = {
    'manual': ('recipe', 'master_asset_ids', 'project_name', 'num_versions', 'bgm_mode', 'auto_delivery_enabled'),
    'automation': ('name', 'scene_type', 'delivery_enabled'),
}


def run_preflight(chain, *, capability_probe=None, asset_probe=None):
    chain = chain or {}; payload = chain.get('payload') or {}; mode = chain.get('execution_mode', 'manual')
    missing = [key for key in REQUIRED_BY_MODE.get(mode, ()) if key not in payload]
    checks = [{'name': key, 'status': 'passed' if key in payload else 'failed'} for key in REQUIRED_BY_MODE.get(mode, ())]
    if payload.get('auto_delivery_enabled') is True or payload.get('delivery_enabled') is True:
        checks.append({'name': 'delivery_disabled', 'status': 'failed'})
    if capability_probe:
        checks.append(capability_probe(chain))
    if asset_probe:
        checks.append(asset_probe(chain))
    failed = [c for c in checks if c['status'] != 'passed']
    return {'status': 'passed' if not failed else 'blocked', 'checks': checks, 'missing_fields': missing, 'blocked_reason': 'PREFLIGHT_FAILED' if failed else None}

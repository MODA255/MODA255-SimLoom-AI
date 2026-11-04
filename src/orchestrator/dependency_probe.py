def build_dependency_probe(adapter, chain):
    checks = []
    try:
        adapter.probe_capabilities()
        checks.append({'name': 'api_capabilities', 'status': 'passed'})
    except Exception as exc:
        checks.append({'name': 'api_capabilities', 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
    payload = chain.get('payload') or {}
    if payload.get('master_asset_ids'):
        checks.append({'name': 'master_asset_ids_present', 'status': 'passed', 'count': len(payload['master_asset_ids'])})
    else:
        checks.append({'name': 'master_asset_ids_present', 'status': 'failed'})
    return checks

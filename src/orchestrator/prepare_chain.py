from .preflight import run_preflight
from .dependency_probe import build_dependency_probe
from .failure_attribution import attribute_failure


def prepare_chain(adapter, chain):
    probe = build_dependency_probe(adapter, chain)
    result = run_preflight(chain, capability_probe=lambda _: next((x for x in probe if x['name'] == 'api_capabilities'), {'name': 'api_capabilities', 'status': 'failed'}), asset_probe=lambda _: next((x for x in probe if x['name'] == 'master_asset_ids_present'), {'name': 'master_asset_ids_present', 'status': 'failed'}))
    if result['status'] == 'blocked':
        result['failure_attribution'] = attribute_failure({'creation_status': None, 'probe_failed': True, 'remote_error': result['blocked_reason']})
    return result

from .evidence_adapter import merge_phase_node_evidence


def resolve_node_evidence(phases, nodes_response):
    """返回节点证据及来源，区分 404、空数组和真实节点。"""
    if isinstance(nodes_response, Exception):
        items = merge_phase_node_evidence(phases, [])
        return {'status': 'collection_failed', 'source': 'phases_fallback', 'http_status': getattr(getattr(nodes_response, 'response', None), 'status_code', None), 'error': str(nodes_response), 'items': items}
    if isinstance(nodes_response, dict) and nodes_response.get('status') in {404, 410}:
        items = merge_phase_node_evidence(phases, [])
        return {'status': 'fallback', 'source': 'phases_fallback', 'http_status': nodes_response.get('status'), 'items': items}
    if isinstance(nodes_response, dict):
        items = nodes_response.get('items') or []
        if items:
            return {'status': 'available', 'source': 'nodes_api', 'http_status': nodes_response.get('status', 200), 'items': items}
        return {'status': 'empty', 'source': 'nodes_api', 'http_status': nodes_response.get('status', 200), 'items': []}
    items = nodes_response or []
    return {'status': 'available' if items else 'empty', 'source': 'nodes_api', 'http_status': 200, 'items': items}

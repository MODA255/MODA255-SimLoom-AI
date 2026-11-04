from .cleanup import CleanupCoordinator


def cleanup_and_verify(adapter, refs):
    result = CleanupCoordinator(adapter).cleanup(refs)
    result['verification'] = {'remaining_resource_count': len(result.get('orphan_resource_ids') or []), 'verified': not result.get('orphan_resource_ids')}
    return result

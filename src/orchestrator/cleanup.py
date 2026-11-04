def asset_cleanup_decision(asset):
    asset=asset or {}; role=asset.get('folder_role'); refs=asset.get('referenced_task_ids') or []
    if role in {'baseline','historical'}: return {'status':'blocked','reason':'BASELINE_PROTECTED','delete_remote':False,'asset_id':asset.get('asset_id')}
    if refs: return {'status':'blocked','reason':'ASSET_STILL_REFERENCED','delete_remote':False,'asset_id':asset.get('asset_id')}
    if int(asset.get('age_days') or 0) < int(asset.get('retention_days') or 30): return {'status':'retained','reason':'RETENTION_NOT_REACHED','delete_remote':False,'asset_id':asset.get('asset_id')}
    return {'status':'candidate','reason':'DYNAMIC_RETENTION_REACHED','delete_remote':False,'asset_id':asset.get('asset_id')}


class CleanupCoordinator:
    """Best-effort, ordered and idempotent cleanup for dynamic test resources."""
    ORDER = ('child_task', 'segment', 'session', 'parent_task', 'task', 'asset')

    def __init__(self, adapter):
        self.adapter = adapter

    def cleanup(self, refs):
        refs = refs or {}
        items = []
        for kind in self.ORDER:
            key = {'parent_task':'parent_task_id','task':'task_id','asset':'asset_ids', 'child_task':'child_task_ids', 'segment':'segment_ids', 'session':'session_ids'}[kind]
            value = refs.get(key)
            values = value if isinstance(value, list) else [value] if value else []
            for resource_id in values: items.append((kind, str(resource_id)))
        if not items: return {'cleanup_status':'skipped','attempts':0,'cleanup_resources':[],'cleanup_failures':[],'orphan_resource_ids':[]}
        attempts=[]; failures=[]; orphans=[]
        for kind, resource_id in items:
            try:
                response = self.adapter.cleanup_resource(kind, resource_id)
                attempts.append({'kind':kind,'resource_id':resource_id,'status':'passed','response':response})
            except Exception as exc:
                failures.append({'kind':kind,'resource_id':resource_id,'error_type':type(exc).__name__,'detail':str(exc)})
                orphans.append(resource_id)
        return {'cleanup_status':'passed' if not failures else 'partial','attempts':len(attempts)+len(failures),'cleanup_resources':attempts,'cleanup_failures':failures,'orphan_resource_ids':orphans}

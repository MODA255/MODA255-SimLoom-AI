from .remote_materials import _items


def probe_material_folders(adapter, folders):
    folders=folders or {}; result={'status':'ready','remote_write_performed':False,'folders':{}}
    for name,key in (('dynamic','dynamic_folder_id'),('baseline','baseline_folder_id')):
        folder_id=folders.get(key)
        if not folder_id or not adapter:
            return {'status':'blocked','reason':'ENVIRONMENT_BLOCKED','remote_write_performed':False,'folders':result['folders']}
        try: assets=_items(adapter.list_assets(folder_id))
        except Exception as exc: return {'status':'blocked','reason':'ENVIRONMENT_BLOCKED','detail':str(exc),'remote_write_performed':False,'folders':result['folders']}
        normalized=[dict(a) for a in assets]; ready=[a for a in normalized if str(a.get('status','')).lower() in {'active','ready'}]
        result['folders'][name]={'folder_id':folder_id,'remote_count':len(normalized),'ready_count':len(ready),'asset_ids':[str(a.get('asset_id') or a.get('id')) for a in ready]}
    return result

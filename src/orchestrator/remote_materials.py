def _items(value):
    if isinstance(value, dict):
        for key in ('items','assets','data'):
            if key in value: return _items(value[key])
        return []
    return value if isinstance(value, list) else []


def _normalize(asset, folder_id):
    asset=dict(asset or {}); return {**asset, 'asset_id':str(asset.get('asset_id') or asset.get('id') or ''), 'folder_id':asset.get('folder_id') or folder_id, 'status':str(asset.get('status') or '').lower(), 'tags':list(asset.get('tags') or [])}


class RemoteMaterialSelector:
    def __init__(self, adapter): self.adapter=adapter
    def select(self, folder_id, filters=None, stale_assets=None):
        try: remote=_items(self.adapter.list_assets(folder_id))
        except Exception as exc: return {'status':'blocked','reason':'REMOTE_MATERIAL_QUERY_FAILED','detail':str(exc),'assets':[]}
        assets=[_normalize(a,folder_id) for a in remote if str(a.get('folder_id') or folder_id)==str(folder_id) and str(a.get('status') or '').lower() in {'active','ready'}]
        for key,value in (filters or {}).items():
            if key in {'ip_name','brand_name','product_name'}:
                assets=[a for a in assets if a.get(key)==value or any(str(t).startswith(f'{key}=') and str(t).split('=',1)[1]==str(value) for t in a.get('tags',[]))]
            else:
                assets=[a for a in assets if a.get(key)==value or value in a.get('tags',[])]
        if filters and filters.get('ip_name'):
            ips={a.get('ip_name') for a in assets if a.get('ip_name')}
            if len(ips)>1: return {'status':'blocked','reason':'IP_EVIDENCE_MISMATCH','assets':[],'evidence':{'ip_names':sorted(ips)}}
        return {'status':'ready','assets':assets,'evidence':{'folder_id':folder_id,'remote_count':len(remote),'ready_count':len(assets),'filters':filters or {}}}


def select_ready_assets(adapter, folder_id, filters=None): return RemoteMaterialSelector(adapter).select(folder_id, filters)


def select_ip_assets(adapter, folder_id, ip_name, automated=False, brand_name=None, product_name=None):
    from .ip_material_gate import validate_ip_materials
    filters={'ip_name':ip_name}
    if brand_name: filters['brand_name']=brand_name
    if product_name: filters['product_name']=product_name
    result=RemoteMaterialSelector(adapter).select(folder_id, filters)
    if result.get('status') != 'ready': return result
    gate=validate_ip_materials(result['assets'], automated=automated)
    result['gate']=gate
    if not gate['valid']:
        result['status']='blocked'; result['reason']='TEST_DATA_BLOCKED'
    return result

import pytest
from orchestrator.remote_materials import RemoteMaterialSelector, select_ready_assets

class Adapter:
    def __init__(self, payload=None, error=None): self.payload=payload or {}; self.error=error
    def list_assets(self, folder_id):
        if self.error: raise self.error
        return self.payload


def test_selects_only_remote_ready_assets_without_local_fallback():
    adapter=Adapter({'items':[{'id':'a1','folder_id':'f','status':'active'}, {'id':'a2','folder_id':'f','status':'processing'}]})
    result=select_ready_assets(adapter, 'f')
    assert result['status']=='ready'
    assert [x['asset_id'] for x in result['assets']] == ['a1']


def test_remote_query_failure_is_environment_blocked():
    result=select_ready_assets(Adapter(error=RuntimeError('offline')), 'f')
    assert result['status']=='blocked'
    assert result['reason']=='REMOTE_MATERIAL_QUERY_FAILED'


def test_selector_does_not_use_stale_asset_argument():
    adapter=Adapter({'items':[]})
    result=RemoteMaterialSelector(adapter).select('f', stale_assets=[{'id':'old','status':'active'}])
    assert result['assets'] == []

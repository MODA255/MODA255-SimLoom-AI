import json
from pathlib import Path
from orchestrator.material_probe import probe_material_folders


def test_probe_reports_two_folder_counts_without_writes():
    class Adapter:
        def list_assets(self, folder_id): return {'items':[{'id':folder_id+'-a','status':'active'}]}
    result=probe_material_folders(Adapter(), {'dynamic_folder_id':'dynamic','baseline_folder_id':'baseline'})
    assert result['status']=='ready'
    assert result['folders']['dynamic']['ready_count']==1
    assert result['folders']['baseline']['ready_count']==1
    assert result['remote_write_performed'] is False


def test_probe_blocks_when_folder_missing():
    result=probe_material_folders(None, {'dynamic_folder_id':'','baseline_folder_id':'baseline'})
    assert result['status']=='blocked'
    assert result['reason']=='ENVIRONMENT_BLOCKED'

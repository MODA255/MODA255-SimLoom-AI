from orchestrator.ingest_suite import run_url_ingest


def test_url_ingest_writes_independent_result(tmp_path):
    class Adapter:
        def ingest_url(self,p): return {'id':'a1'}
        def get_asset(self,i): return {'id':i,'status':'ready','source_url':'https://x'}
        def get_asset_progress(self,i): return {'status':'ready','percent':100}
    result=run_url_ingest(Adapter(),{'chain_id':'ingest_url','folder_id':'f','source_url':'https://x','payload':{}},tmp_path)
    assert result['test_status']=='passed'
    assert (tmp_path/'ingest_url'/'ingest-result.json').exists()

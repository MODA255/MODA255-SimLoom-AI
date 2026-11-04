from orchestrator.file_ingest import run_file_ingest


def test_file_ingest_persists_submission(tmp_path):
    class Adapter:
        def ingest_file(self,p): return {'id':'a'}
    result=run_file_ingest(Adapter(),{'chain_id':'ingest_file','payload':{'path':'x'}},tmp_path)
    assert result['test_status']=='pending'

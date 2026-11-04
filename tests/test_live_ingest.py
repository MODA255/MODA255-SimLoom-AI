from orchestrator.live_ingest import run_live_ingest


def test_live_ingest_blocks_and_persists_source_probe(tmp_path):
    result=run_live_ingest(object(),{'chain_id':'ingest_live','source_urls':['bad'],'folder_id':'f'},tmp_path)
    assert result['test_status']=='blocked'
    assert (tmp_path/'ingest_live'/'source-probe.json').exists()

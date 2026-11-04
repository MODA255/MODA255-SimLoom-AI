from orchestrator.daily_pipeline import collect_chain_evidence


class Adapter:
    def get_task(self, task_id): return {"id":task_id,"status":"completed","recipe":"auto_theme_discovery","script":"脚本"}
    def get_phases(self, task_id): return [{"phase_name":"select","status":"completed","metrics":{"selected_count":1},"output_data":{}}]
    def get_nodes(self, task_id): return {"items":[{"key":"select","status":"completed","input":{"n":1},"output":{"n":1}}]}
    def get_videos(self, task_id): return [{"id":"v","status":"created","oss_url":"https://oss/v","duration":1,"resolution":"1080x1920","file_size":1,"clip_ids":[{"clip_id":"c","asset_clip_id":"s","source_start":0,"source_end":1,"start_time":0,"end_time":1}]}]
    def get_transcript(self, task_id, video_id): return None
    def get_diagnostics(self, task_id, names): return {"pipeline.log":{"status":200,"text":"ok"}}


def test_evidence_collection_records_sources_and_missing_reasons():
    result = collect_chain_evidence(Adapter(), {"task_id":"t","expected_video_count":1})
    assert result["evidence_collection"]["status"] in {"partial", "completed"}
    assert "phases" in result["evidence_collection"]["sources_checked"]
    assert result["lineage"]
    assert result["text_artifacts"]
    assert result["diagnostics"]

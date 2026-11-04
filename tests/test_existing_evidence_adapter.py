from orchestrator.daily_pipeline import collect_chain_evidence


class Adapter:
    def get_task(self, task_id):
        return {"id":task_id,"status":"completed","recipe":"auto_theme_discovery","delivery_enabled":False,"auto_delivery_enabled":False,"completed_phases":1,"total_phases":1}
    def get_videos(self, task_id):
        return [{"id":"v1","status":"created","oss_url":"https://oss/v.mp4","duration":1,"resolution":"1080x1920","file_size":1,"clip_ids":[]}]
    def get_nodes(self, task_id):
        return {"items":[{"key":"select","status":"completed","input":{"count":2},"output":{"selected":2},"calls":[]} ]}
    def get_transcript(self, task_id, video_id):
        return None


def test_collect_chain_evidence_uses_existing_nodes_api_as_fallback():
    result = collect_chain_evidence(Adapter(), {"task_id":"t1","expected_video_count":1})
    assert result["nodes"][0]["key"] == "select"
    assert result["evidence_source"] == "legacy_nodes_api"
    assert result["local_media_files"] == []

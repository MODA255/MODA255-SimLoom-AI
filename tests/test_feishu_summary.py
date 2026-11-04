from orchestrator.feishu_summary import build_feishu_summary


def test_feishu_summary_reports_blocked_chains_and_evidence_counts():
    result = build_feishu_summary({'summary': {'passed': 3, 'failed': 0, 'warnings': 0, 'poll_event_count': 8, 'node_count': 4, 'diagnostic_count': 6, 'screenshot_count': 3, 'video_count': 3}, 'chains': [{'chain_id': 'auto_ecom', 'verdict': {'test_status': 'blocked'}}]}, 'https://report.test')
    assert result['msg_type'] == 'interactive'
    content = str(result)
    assert 'auto_ecom' in content
    assert '轮询' in content
    assert 'https://report.test' in content

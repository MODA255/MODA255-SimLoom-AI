import httpx
from orchestrator.video_health import check_remote_video


def test_video_health_reports_missing_url():
    result = check_remote_video({})
    assert result['verdict'] == 'failed'
    assert result['error'] == 'VIDEO_URL_MISSING'


def test_video_health_checks_remote_headers(monkeypatch):
    class Response:
        status_code = 200
        headers = {'content-type': 'video/mp4', 'content-length': '42'}
        @property
        def is_success(self): return True
    monkeypatch.setattr(httpx, 'head', lambda *args, **kwargs: Response())
    result = check_remote_video({'oss_url': 'https://example.test/video.mp4'})
    assert result['verdict'] == 'passed'
    assert result['content_type'] == 'video/mp4'

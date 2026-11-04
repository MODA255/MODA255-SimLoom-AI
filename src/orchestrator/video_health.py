from urllib.parse import urlparse
import httpx


def check_remote_video(video, timeout=20):
    url = video.get('oss_url') or video.get('remote_url') or video.get('url')
    result = {'url': url, 'http_status': None, 'content_type': None, 'content_length': None, 'verdict': 'insufficient_evidence', 'error': None}
    if not url:
        result['verdict'] = 'failed'; result['error'] = 'VIDEO_URL_MISSING'; return result
    try:
        response = httpx.head(url, follow_redirects=True, timeout=timeout)
        result.update({'http_status': response.status_code, 'content_type': response.headers.get('content-type'), 'content_length': response.headers.get('content-length')})
        result['verdict'] = 'passed' if response.is_success and (result['content_type'] or '').startswith('video/') else 'failed'
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    return result

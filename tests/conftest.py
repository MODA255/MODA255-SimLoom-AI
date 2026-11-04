"""Explicit test-only source rules; production defaults remain restrictive."""
import pytest
from orchestrator.policy import SourcePolicy

@pytest.fixture
def explicit_source_fixture():
    policy = SourcePolicy.from_dict({
        "live": {"allowed_platforms": ["douyin", "weibo"], "rules": {
            "douyin": [{"host": "example.com", "path": "/", "types": ["live_url"]}],
        }},
        "content": {"allowed_url_types": ["direct_video", "verified_ingest_platform"], "rules": {
            "youtube": [{"host": "example.com", "path": "/", "types": ["verified_ingest_platform"]}, {"host":"fixed.test", "path":"/source", "types":["direct_video"]}, {"host":"x.test", "path":"/v", "types":["verified_ingest_platform"]}],
        }},
    })
    return policy

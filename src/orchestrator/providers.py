from dataclasses import dataclass
from typing import Any
import httpx

@dataclass
class SearchProvider:
    base_url: str
    token_env: str | None = None
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        headers = {}
        import os
        if self.token_env and os.getenv(self.token_env): headers['Authorization'] = 'Bearer ' + os.environ[self.token_env]
        r = httpx.get(self.base_url, params={'q': query, 'limit': limit}, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        return data.get('results', data if isinstance(data, list) else [])

@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model: str
    token_env: str
    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        import os
        token = os.getenv(self.token_env)
        if not token: raise RuntimeError('AUTH_REQUIRED')
        r = httpx.post(self.base_url.rstrip('/') + '/chat/completions', json={'model': self.model, 'messages': messages}, headers={'Authorization': 'Bearer '+token}, timeout=60)
        r.raise_for_status(); return r.json()

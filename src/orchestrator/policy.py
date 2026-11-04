import hashlib, json, re

def sanitize(value):
 s=json.loads(json.dumps(value))
 def walk(x):
  if isinstance(x,dict): return {k: ('[REDACTED]' if any(t in k.lower() for t in ('token','cookie','secret','authorization')) else walk(v)) for k,v in x.items()}
  if isinstance(x,list): return [walk(v) for v in x]
  if isinstance(x,str): return re.sub(r'(?i)(bearer\s+)[\w.-]+',r'\1[REDACTED]',x)
  return x
 return walk(s)

def validate_dynamic_payload(payload,fixed):
 for key,val in fixed.items():
  if key in payload and payload[key] != val: raise ValueError(f'fixed field changed: {key}')
 return payload


from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SourceDecision:
    allowed: bool = False
    reason: str = 'unverified'
    status: str = 'unverified'
    platform: str | None = None
    url_type: str | None = None


class SourcePolicy:
    """Trusted local rules only; provider metadata never grants eligibility."""
    def __init__(self, data, config_path=None):
        self.data = data
        self.config_path = Path(config_path) if config_path else Path('matrixgen-source-policy.json')

    def source_type_allowed(self, source_type):
        return source_type in {'douyin_video', 'douyin_live', 'weibo_video'}

    @classmethod
    def from_dict(cls, data):
        return cls(json.loads(json.dumps(data)))

    _cache = {}

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else Path(__file__).resolve().parents[2] / 'configs/matrixgen-source-policy.json'
        path = path.resolve()
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
            if key in cls._cache:
                return cls(cls._cache[key][0], path)
            data = json.loads(path.read_text(encoding='utf-8'))
            data = data if isinstance(data, dict) else {}
            cls._cache = {key: (json.loads(json.dumps(data)),)}
            return cls(data, path)
        except (OSError, ValueError):
            return cls({}, path)

    def validate_content(self, url, metadata=None):
        return self._validate(url, 'content', metadata or {})

    def validate_probe_target(self, url, metadata=None):
        """Eligibility for read-only probing, never a grant of source trust."""
        import ipaddress
        metadata = metadata or {}
        decision = self.validate_content(url, metadata)
        if decision.reason == 'invalid_url':
            return decision
        p = urlsplit(url)
        host = (p.hostname or '').lower().rstrip('.')
        if (host in ('live.douyin.com', 'live.weibo.com')
                or self.validate_live(url).allowed
                or metadata.get('url_type') == 'live_url'
                or metadata.get('content_type') == 'live'):
            return SourceDecision(reason='live_requires_live_source_search', status='blocked')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host or host.endswith(('.localhost', '.local', '.internal')):
                return SourceDecision(reason='unsafe_probe_target', status='blocked')
        else:
            if not address.is_global:
                return SourceDecision(reason='unsafe_probe_target', status='blocked')
        return SourceDecision(True, 'probe_required', 'unverified', decision.platform)

    def validate_live(self, url, metadata=None):
        return self._validate(url, 'live', metadata or {})

    def _validate(self, url, kind, metadata):
        try:
            if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
                raise ValueError()
            p = urlsplit(url)
            if p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or p.password is not None or p.port not in (None, 80, 443):
                raise ValueError()
            # Reject encoded and dot-segment paths rather than normalize across trust boundaries.
            if '%' in p.path or any(s in ('.', '..') for s in p.path.split('/')):
                raise ValueError()
        except ValueError:
            return SourceDecision(reason='invalid_url', status='blocked')
        section = self.data.get(kind, {})
        rules = section.get('rules', {}) if isinstance(section, dict) else {}
        if not isinstance(rules, dict):
            return SourceDecision()
        for platform, entries in rules.items():
            if kind == 'live' and platform not in ('douyin', 'weibo'):
                continue
            if 'allowed_platforms' in section and platform not in section['allowed_platforms']:
                continue
            for rule in entries if isinstance(entries, list) else []:
                if not isinstance(rule, dict):
                    continue
                host, path, types = rule.get('host'), rule.get('path'), rule.get('types', [])
                if not isinstance(host, str) or not isinstance(path, str) or not path.startswith('/') or not isinstance(types, list) or len(types) != 1:
                    continue
                url_type = types[0]
                valid_types = ('live_url',) if kind == 'live' else ('direct_video', 'verified_ingest_platform')
                if url_type not in valid_types or ('allowed_url_types' in section and url_type not in section['allowed_url_types']):
                    continue
                if metadata.get('url_type') not in (None, url_type):
                    continue
                actual = p.path or '/'
                match = actual.startswith(path) if path.endswith('/') else actual == path
                if p.hostname.lower() == host.lower() and match:
                    return SourceDecision(True, 'candidate_only' if kind == 'live' else 'verified_rule', 'candidate_only' if kind == 'live' else 'verified', platform, url_type)
        return SourceDecision()

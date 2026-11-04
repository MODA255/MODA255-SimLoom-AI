import copy, hashlib, json, os
from pathlib import Path
from dataclasses import dataclass, field


def _parse_env_file(path):
    values = {}
    if not path or not Path(path).exists():
        return values
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _apply_env(data, env_values):
    mappings = {
        'MATRIXGEN_BASE_URL': ('target', 'base_url'),
        'MATRIXGEN_API_PREFIX': ('target', 'api_prefix'),
        'MATRIXGEN_TOKEN': ('auth', 'matrixgen_token'),
        'MATRIXGEN_REFRESH_TOKEN': ('auth', 'refresh_token'),
        'MATRIXGEN_IDENTIFIER': ('auth', 'identifier'),
        'MATRIXGEN_EMAIL': ('auth', 'email'),
        'MATRIXGEN_PASSWORD': ('auth', 'password'),
        'MATRIXGEN_FOLDER_ID': ('profiles', 'folder_id'),
        'MATRIXGEN_TEMPLATE_ID': ('profiles', 'template_id'),
        'MATRIXGEN_TEMPLATE_VERSION': ('profiles', 'template_version'),
        'MATRIXGEN_RECIPE': ('profiles', 'recipe'),
        'MATRIXGEN_BRAND_ID': ('business', 'brand_id'),
        'MATRIXGEN_PRODUCT_ID': ('business', 'product_id'),
        'MATRIXGEN_MECHANISM_ID': ('business', 'mechanism_id'),
        'MATRIXGEN_IP_ID': ('business', 'ip_id'),
        'TEST_SOURCE_URL': ('test_input', 'source_url'),
    }
    result = copy.deepcopy(data)
    for key, path in mappings.items():
        if key in ('MATRIXGEN_TOKEN', 'MATRIXGEN_REFRESH_TOKEN', 'MATRIXGEN_IDENTIFIER', 'MATRIXGEN_EMAIL', 'MATRIXGEN_PASSWORD'):
            continue
        value = env_values.get(key) or os.getenv(key)
        if value:
            target = result
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
    return result

class ConfigError(ValueError): pass

@dataclass(frozen=True)
class Config:
    data: dict
    state_db: str
    mode: str
    credentials: dict = field(default_factory=dict, repr=False, compare=False)
    def snapshot(self): return copy.deepcopy(self.data)
    @property
    def required_sources(self): return self.data.get('required_sources', ['live','link'])

def load_config(source, env_file=None):
    data = json.loads(open(source).read()) if isinstance(source, (str, os.PathLike)) else copy.deepcopy(source)
    if env_file is None and isinstance(source, (str, os.PathLike)):
        candidate = Path(source).parent / '.env'
        env_file = candidate if candidate.exists() else None
    env_values = _parse_env_file(env_file)
    data = _apply_env(data, env_values)
    data.setdefault('auth', {})
    data['auth']['token_env'] = data['auth'].get('token_env', 'MATRIXGEN_TOKEN')
    data['auth']['refresh_token_env'] = data['auth'].get('refresh_token_env', 'MATRIXGEN_REFRESH_TOKEN')
    data['auth']['identifier_env'] = data['auth'].get('identifier_env', 'MATRIXGEN_IDENTIFIER')
    data['auth']['email_env'] = data['auth'].get('email_env', 'MATRIXGEN_EMAIL')
    data['auth']['password_env'] = data['auth'].get('password_env', 'MATRIXGEN_PASSWORD')
    data['auth'].pop('matrixgen_token', None)
    data['auth'].pop('refresh_token', None)
    if data.get('mode') not in ('simulation','remote_live'): raise ConfigError('mode must be simulation or remote_live')
    auth = data.get('auth', {})
    secret_keys = ('token', 'cookie', 'secret')
    for key in secret_keys:
        if key in auth and auth[key] not in (None, ''):
            raise ConfigError('credentials must be references, not secrets')
    if data.get('profiles', {}).get('publish') is not False: raise ConfigError('publishing is disabled')
    if not data.get('state_db'): raise ConfigError('state_db required')
    credentials = {key: os.getenv(data['auth'].get(name, default)) or env_values.get(data['auth'].get(name, default), '') for key, name, default in (('token', 'token_env', 'MATRIXGEN_TOKEN'), ('refresh_token', 'refresh_token_env', 'MATRIXGEN_REFRESH_TOKEN'), ('identifier', 'identifier_env', 'MATRIXGEN_IDENTIFIER'), ('email', 'email_env', 'MATRIXGEN_EMAIL'), ('password', 'password_env', 'MATRIXGEN_PASSWORD'))}
    return Config(data, data['state_db'], data['mode'], credentials)

def snapshot_hash(data): return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',',':')).encode()).hexdigest()

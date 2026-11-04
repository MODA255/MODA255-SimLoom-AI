from pathlib import Path
import json

REQUIRED_MANUAL = {'recipe', 'master_asset_ids', 'project_name', 'num_versions', 'bgm_mode', 'auto_delivery_enabled'}


def load_executable_suite(path):
    suite = json.loads(Path(path).read_text(encoding='utf-8'))
    for chain in suite.get('chains', []):
        if chain.get('execution_mode') == 'manual':
            payload = chain.get('payload') or {}
            missing = sorted(REQUIRED_MANUAL - payload.keys())
            if missing:
                chain['blocked_reason'] = 'MANUAL_PAYLOAD_MISSING_FIELDS'
                chain['blocked_fields'] = missing
    return suite

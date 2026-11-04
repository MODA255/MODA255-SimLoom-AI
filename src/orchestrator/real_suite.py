import json
from pathlib import Path
from .adapters import RemoteAdapter
from .config import load_config
from .execution_ledger import ExecutionLedger
from .suite_executor import run_suite_isolated
from .chain_runners import build_chain_runner


def run_real_suite(config_path, suite_path, output_dir):
    raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    config = load_config(raw, env_file=Path(config_path).parent.parent / '.env')
    suite = json.loads(Path(suite_path).read_text(encoding='utf-8'))
    root = Path(output_dir); ledger = ExecutionLedger(root / 'chains'); adapter = RemoteAdapter(config)
    def runner(chain):
        return build_chain_runner(adapter, chain, ledger=ledger)
    result = run_suite_isolated(suite.get('chains') or [], runner, ledger)
    (root / 'suite-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return result

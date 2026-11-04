from pathlib import Path
import json
from datetime import datetime, timezone


class ExecutionLedger:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def append_poll(self, chain_id, event):
        path = self.root / chain_id / 'poll-events.jsonl'; path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

    def write_snapshot(self, chain_id, name, value):
        path = self.root / chain_id / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    def write_result(self, chain_id, result):
        result = dict(result); result.setdefault('recorded_at', datetime.now(timezone.utc).isoformat())
        self.write_snapshot(chain_id, 'execution-result.json', result)
        return result

import json
from pathlib import Path
from .material_ingest import ingest_and_wait_ready, MaterialIngestError


def run_url_ingest(adapter, spec, output_dir):
    root=Path(output_dir);root.mkdir(parents=True,exist_ok=True);chain=spec.get('chain_id','ingest_url');chain_dir=root/chain;chain_dir.mkdir(parents=True,exist_ok=True)
    (chain_dir/'submission.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        result=ingest_and_wait_ready(adapter,spec.get('payload') or {},spec['folder_id'],source_url=spec.get('source_url'),title=spec.get('title'),timeout=spec.get('timeout_seconds',1800),poll=spec.get('poll_interval_seconds',20),operation_key=spec.get('operation_key'))
        status={'chain_id':chain,'execution_status':'completed','test_status':'passed' if result['status']=='ready' else 'failed','ingest':result}
    except MaterialIngestError as exc:
        status={'chain_id':chain,'execution_status':'failed','test_status':'failed','failure':{'code':exc.code,'message':exc.detail},'evidence':exc.evidence}
    (chain_dir/'ingest-result.json').write_text(json.dumps(status,ensure_ascii=False,indent=2,default=str),encoding='utf-8');return status

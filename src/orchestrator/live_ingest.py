import json
from pathlib import Path
from .live_source_selector import choose_live_source
from .material_ingest import ingest_and_wait_ready, MaterialIngestError


def run_live_ingest(adapter, spec, output_dir):
    root=Path(output_dir);root.mkdir(parents=True,exist_ok=True);chain=spec.get('chain_id','ingest_live');d=root/chain;d.mkdir(parents=True,exist_ok=True)
    selection=choose_live_source(spec.get('source_urls') or [],search=spec.get('source_search'))
    (d/'source-probe.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2),encoding='utf-8')
    if selection.get('status')!='passed':
        result={'chain_id':chain,'execution_status':'blocked','test_status':'blocked','blocked_reason':'LIVE_SOURCE_UNAVAILABLE','source_probe':selection};(d/'ingest-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
    payload = dict(spec.get('payload') or {})
    payload = {'url': selection['selected_url'], 'source_type': 'live_recording', **({'folder_id': spec.get('folder_id')} if spec.get('folder_id') else {})}
    try:
        ingest=ingest_and_wait_ready(adapter,payload,spec['folder_id'],source_url=selection['selected_url'],timeout=spec.get('timeout_seconds',1800),poll=spec.get('poll_interval_seconds',20),operation_key=spec.get('operation_key'))
        result={'chain_id':chain,'execution_status':'completed','test_status':'passed','source_probe':selection,'ingest':ingest}
    except MaterialIngestError as exc:
        result={'chain_id':chain,'execution_status':'failed','test_status':'failed','source_probe':selection,'failure':{'code':exc.code,'message':exc.detail},'evidence':exc.evidence}
    (d/'ingest-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));return result

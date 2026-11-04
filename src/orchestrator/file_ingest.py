from pathlib import Path
import json


def run_file_ingest(adapter, spec, output_dir):
    if spec.get('phase1'):
        from .phase1_ingest_runners import run_upload_ingest
        return run_upload_ingest(adapter, spec, output_dir)
    root=Path(output_dir);chain=spec.get('chain_id','ingest_file');d=root/chain;d.mkdir(parents=True,exist_ok=True);(d/'submission.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        result=adapter.ingest_file(spec.get('payload') or {})
        status={'chain_id':chain,'execution_status':'created','test_status':'pending','submission':result,
                'preflight':{'status':'passed'},'submit':{'status':'accepted'},'poll':{'status':'pending'},
                'accept':{'status':'inconclusive'},'resources':[],'downstream_ready':False}
    except Exception as exc:
        status={'chain_id':chain,'execution_status':'failed','test_status':'failed','failure':{'code':type(exc).__name__,'message':str(exc)},
                'preflight':{'status':'passed'},'submit':{'status':'failed'},'poll':{'status':'not_started'},
                'accept':{'status':'failed'},'resources':[],'downstream_ready':False}
    (d/'ingest-result.json').write_text(json.dumps(status,ensure_ascii=False,indent=2,default=str));return status

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

@dataclass(frozen=True)
class AutomationStatus:
    status: str
    reason: str
    failed_steps: tuple[str, ...] = ()

@dataclass(frozen=True)
class AutomationVerification:
    status: str
    reason: str
    evidence: list[dict[str, Any]]


def _canonical(value):
    value=str(value or '').strip().lower().replace('-','_').replace(' ','_')
    return {'success':'completed','succeeded':'completed','done':'completed','closed':'completed','pass':'completed','passed':'completed','fail':'failed','error':'failed','cancelled':'cancelled'}.get(value,value)


def classify_automation_scope(task):
    task=task or {}; source=str(task.get('creation_source') or '').strip().lower()
    if source=='manual_vfc': return 'manual_vfc'
    if source in {'ip_promo_automation','ip_promo'}: return 'ip_promo_automation'
    if source in {'automation_task','automation'}: return 'automation_task'
    if task.get('parent_task_id') and str(task.get('parent_task_id')).strip(): return 'automation_task'
    return 'unknown'


def automation_templates():
    return ('ecom_mix','theme_discovery','ip_promo')


def normalize_automation_result(raw):
    raw = dict(raw or {})
    normalized = dict(raw)
    normalized['automation_scope'] = classify_automation_scope(raw)
    if normalized['automation_scope'] == 'unknown' and raw.get('kind') in {'ecom_mix','theme_discovery'}:
        normalized['automation_scope'] = 'automation_task'
    for key in ('parent','video','delivery'):
        value = raw.get(key) or {}
        normalized[key] = {**value, 'status': _canonical(value.get('status'))}
    normalized['children'] = [{**child, 'status': _canonical(child.get('status'))} for child in raw.get('children', [])]
    if normalized['automation_scope'] == 'manual_vfc' or str((raw.get('parent') or {}).get('creation_source', '')).lower() == 'manual_vfc':
        normalized.update(automation_scope='manual_vfc', status='blocked', reason='MANUAL_VFC_NOT_AUTOMATION')
    elif normalized['automation_scope'] == 'ip_promo_automation' and str(raw.get('clip_status', '')).lower() in {'clip_done', 'done', 'completed'} and str(raw.get('sync_status', '')).lower() == 'blocked':
        normalized.update(status='blocked', reason='IP_CLIP_DONE_SYNC_BLOCKED')
    return normalized

def evaluate_automation_status(result):
    result=normalize_automation_result(result); scope=result.get('automation_scope')
    if scope == 'manual_vfc': return AutomationStatus('blocked','manual_vfc is not automation')
    if scope == 'unknown' and result.get('kind') in automation_templates(): scope='automation_task'
    if scope not in {'automation_task','ip_promo_automation'}: return AutomationStatus('blocked','automation scope unknown')
    failed=[]
    if (result.get('parent') or {}).get('status') not in {'completed','clip_done'}: failed.append('parent')
    if not result.get('children') or any(c.get('status') not in {'completed','done'} for c in result['children']): failed.append('children')
    if (result.get('video') or {}).get('status') not in {'completed','done'}: failed.append('video')
    if (result.get('delivery') or {}).get('status') not in {'closed','skipped','completed'}: failed.append('delivery')
    return AutomationStatus('failed' if failed else 'passed','automation stages evaluated',tuple(failed))


def _nonempty(value): return value is not None and str(value).strip() != ''

def verify_automation_result(task=None, parent=None):
    task, parent = task or {}, parent or {}
    source = str(task.get('creation_source','')).strip().lower()
    parent_id = task.get('parent_task_id')
    parent_source = str(parent.get('creation_source', '')).strip().lower()
    matches = _nonempty(parent_id) and (not parent.get('id') or str(parent.get('id')) == str(parent_id))
    evidence = [{'field':'creation_source','value':task.get('creation_source')},{'field':'parent_task_id','value':parent_id}]
    if source == 'manual_vfc':
        return AutomationVerification('automation_mismatch','manual_vfc is not an automation creation source',evidence)
    if parent_source == 'manual_vfc':
        return AutomationVerification('blocked','manual_vfc is not an automation creation source',evidence)
    if source in {'automation_task','automation'}:
        return AutomationVerification('automation_success','automation creation was confirmed',evidence)
    if parent_source in {'automation_task','automation'} and not _nonempty(parent_id):
        return AutomationVerification('automation_success','automation parent creation was confirmed',evidence)
    if matches:
        return AutomationVerification('automation_success','automation parent-child relation was confirmed',evidence)
    return AutomationVerification('blocked','automation creation could not be confirmed',evidence)

def extract_frontend_evidence(artifacts_dir):
    from pathlib import Path
    import re
    root=Path(artifacts_dir)
    if not root.exists(): return {'status':'missing','creation_api_confirmed':False,'matches':[]}
    matches=[]
    for p in root.rglob('*'):
        if p.suffix.lower() not in {'.js','.json','.ts','.tsx','.html'}: continue
        try:s=p.read_text(errors='ignore')
        except OSError:continue
        for m in re.finditer(r'.{0,120}automation.{0,160}',s,re.I): matches.append({'file':str(p.relative_to(root)),'snippet':' '.join(m.group(0).split())[:400]})
        if len(matches)>=200:break
    return {'status':'found','creation_api_confirmed':False,'matches':matches[:200]}


def run_automation(adapter,kind,frontend_artifacts,runs_dir):
    from datetime import datetime,timezone
    from pathlib import Path
    evidence=extract_frontend_evidence(frontend_artifacts);result={'kind':kind,'read_only':True,'remote_write_performed':False,'frontend_evidence':evidence,'creation_api_confirmed':False,'status':'blocked','reason':'automation creation API is not confirmed'}
    try:
        if hasattr(adapter,'probe_automation_tasks'):result['probe']={'status':'ok','data':adapter.probe_automation_tasks()}
        else:result['reason']='automation read route is not configured'
    except Exception as exc:result['probe']={'status':'error','error':type(exc).__name__,'detail':str(exc)}
    p=Path(runs_dir)/f'automation-{kind}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}';p.mkdir(parents=True,exist_ok=True)
    result['artifact_path']=str(p/'result.json');(p/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

def persist_verification(result,runs_dir,task):
    from pathlib import Path
    root=Path(runs_dir);root.mkdir(parents=True,exist_ok=True);body={**asdict(result),'task':task,'read_only':True,'remote_write_performed':False};p=root/'verification.json';p.write_text(json.dumps(body,ensure_ascii=False,indent=2));return body

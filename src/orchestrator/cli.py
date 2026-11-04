import json
import os
from .config import load_config
from .repository import Repository
from .engine import Orchestrator
from .content_source_search import ContentSourceSearchInput, ContentSourceSearchSkill
from .live_source_search import LiveSourceSearchInput, LiveSourceSearchSkill
from .topic_generation import TopicGenerationInput, TopicGenerationSkill
from .scenario_assembly import ScenarioAssemblyInput, ScenarioAssemblySkill
from .providers import SearchProvider
from .daily_pipeline import run_daily_pipeline


def _deep_merge(base, override):
    """Merge config mappings without allowing remote references to replace local values."""
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _phase1_remote_config_candidates(config_path):
    """Yield repository-local remote config candidates, without touching network.

    A phase-one run config may live in ``local/phase1`` while the protected
    reference config lives in ``configs``.  Resolve the latter by walking the
    config path's parents (and the current checkout) rather than assuming both
    files share a directory.
    """
    from pathlib import Path

    path = Path(config_path).resolve()
    candidates = [path.parent / "remote-test.local.json"]
    for parent in (path.parent, *path.parents):
        candidates.append(parent / "configs" / "remote-test.local.json")
    candidates.append(Path.cwd() / "configs" / "remote-test.local.json")
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def _merge_phase1_remote_references(raw, config_path):
    """Import local reference routes while preserving every local override.

    This is deliberately a file-only operation.  It never calls the target and
    never writes either the source config or the caller's mapping.
    """
    local = dict(raw or {})
    source = next((candidate for candidate in _phase1_remote_config_candidates(config_path)
                   if candidate.is_file()), None)
    if source is None:
        return local
    remote = json.loads(source.read_text(encoding="utf-8"))
    for section in ("remote_api", "automation", "automation_routes", "cleanup", "cleanup_routes", "auth"):
        if section in remote:
            local[section] = _deep_merge(remote.get(section), local.get(section, {}))
    return local


def _search_provider(config):
    provider = config.data.get("providers", {}).get("search")
    if not provider or provider in ("simulation", None): return None
    if isinstance(provider, dict) and provider.get("base_url"): return SearchProvider(provider["base_url"], provider.get("token_env"))
    return None

def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(prog='orchestrator'); sub=p.add_subparsers(dest='cmd',required=True)
    for n in ('doctor','plan','run'): x=sub.add_parser(n); x.add_argument('--config',required=True)
    x=sub.add_parser('daily-plan'); x.add_argument('--config',required=True); x.add_argument('--input'); x.add_argument('--input-json')
    x=sub.add_parser('daily-run'); x.add_argument('--config',required=True); x.add_argument('--run-id'); x.add_argument('--input'); x.add_argument('--input-json'); x.add_argument('--execute',action='store_true'); x.add_argument('--phase1',action='store_true')
    x=sub.add_parser('phase1-readiness'); x.add_argument('--config',required=True)
    x=sub.add_parser('daily-status'); x.add_argument('--config',required=True); x.add_argument('--run-id',required=True)
    x=sub.add_parser('daily-report'); x.add_argument('--config',required=True); x.add_argument('--run-id',required=True); x.add_argument('--output-dir')
    x=sub.add_parser('daily-evaluate'); x.add_argument('--config',required=True); x.add_argument('--suite',required=True); x.add_argument('--output-dir',default='runs/daily-structured')
    x=sub.add_parser('daily-full-plan'); x.add_argument('--config',required=True); x.add_argument('--input'); x.add_argument('--input-json')
    x=sub.add_parser('daily-full-report'); x.add_argument('--config',required=True); x.add_argument('--input'); x.add_argument('--input-json'); x.add_argument('--plan-file')
    x=sub.add_parser('phase-capabilities'); x.add_argument('--config',required=True)
    x=sub.add_parser('material-capabilities'); x.add_argument('--config',required=True)
    x=sub.add_parser('search-live'); x.add_argument('--config',required=True); x.add_argument('--query',default='')
    x=sub.add_parser('generate-topic'); x.add_argument('--config',required=True)
    x=sub.add_parser('assemble-scenario'); x.add_argument('--config',required=True)
    x=sub.add_parser('search-link'); x.add_argument('--config',required=True); x.add_argument('--query',required=True); x.add_argument('--limit',type=int,default=5)
    x=sub.add_parser('ingest-url'); x.add_argument('--config',required=True); x.add_argument('--url',required=True); x.add_argument('--title',default=''); x.add_argument('--folder-id',default='')
    x=sub.add_parser('diagnose-import-url'); x.add_argument('--config',required=True); x.add_argument('--folder-id',default=''); x.add_argument('--source-type',default='master_tape')
    x=sub.add_parser('build-ecom-payload'); x.add_argument('--config',required=True); x.add_argument('--asset-json',required=True); x.add_argument('--topic',required=True)
    x=sub.add_parser('verify-automation-result'); x.add_argument('--task-json',required=True); x.add_argument('--parent-json'); x.add_argument('--runs-dir',default='runs')
    x=sub.add_parser('run-automation'); x.add_argument('--config',required=True); x.add_argument('--kind',choices=['live','link','ecom'],required=True); x.add_argument('--frontend-artifacts',default='artifacts/frontend-analysis'); x.add_argument('--runs-dir',default='runs')
    for n in ('resume','cancel','report'): x=sub.add_parser(n); x.add_argument('--run-id',required=True); x.add_argument('--config',required=True)
    x=sub.add_parser('review'); x.add_argument('--review-id',required=True); x.add_argument('--decision',required=True); x.add_argument('--config',required=True)
    a=p.parse_args(argv)
    config_dir=os.path.dirname(os.path.abspath(getattr(a, 'config', '.'))); env_file=os.path.join(config_dir,'.env')
    if not os.path.exists(env_file): env_file=os.path.join(os.path.dirname(config_dir),'.env')
    if a.cmd == 'phase1-readiness':
        from .phase1_runner import phase1_readiness
        # Phase-one suites resolve env: references from the config directory's .env.
        from .phase1_suite import _read_config
        raw=_merge_phase1_remote_references(_read_config(a.config), a.config)
        if isinstance(raw.get('phase1'), dict) and isinstance(raw['phase1'].get('suite'), str):
            suite_path = raw['phase1']['suite']
            if not os.path.isabs(suite_path):
                raw['phase1']['suite'] = os.path.join(os.path.dirname(a.config), suite_path)
        out=phase1_readiness(raw, strict=True)
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
    if a.cmd == 'daily-evaluate':
        from .adapters import RemoteAdapter
        raw=json.load(open(a.config,encoding='utf-8')); raw.setdefault('profiles', {})['publish']=False; c=load_config(raw,env_file=env_file)
        suite=json.load(open(a.suite,encoding='utf-8')); chains=suite.get('chains') or []
        allowed={'manual_ecom','manual_ip','manual_theme','auto_ecom','auto_theme','auto_ip'}
        if not chains or any(item.get('chain_id') not in allowed or not item.get('task_id') for item in chains):
            raise SystemExit('Invalid daily chain suite')
        out=run_daily_pipeline(RemoteAdapter(c), chains, a.output_dir)
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
        from .adapters import RemoteAdapter
        from .material_probe import probe_material_folders
        raw=json.load(open(a.config,encoding='utf-8')); c=load_config(raw,env_file=env_file)

        source=c.data.get('material_source',{}); folders={'dynamic_folder_id':os.getenv(source.get('dynamic_folder_id_env',''),''),'baseline_folder_id':os.getenv(source.get('baseline_folder_id_env',''),'')}
        out=probe_material_folders(RemoteAdapter(c), folders)
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0

    if a.cmd == 'phase-capabilities':
        from .capability_probe import probe_phase_routes
        raw=json.load(open(a.config,encoding='utf-8'))
        out=probe_phase_routes(raw)
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0

    if a.cmd == 'verify-automation-result':
        from .automation import verify_automation_result, persist_verification
        task=json.load(open(a.task_json,encoding='utf-8')); parent=json.load(open(a.parent_json,encoding='utf-8')) if a.parent_json else None
        verification=verify_automation_result(task,parent); out=persist_verification(verification,a.runs_dir,task); print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
    if a.cmd == 'run-automation':
        from .adapters import RemoteAdapter
        from .automation import run_automation
        raw=json.load(open(a.config,encoding='utf-8')); raw.setdefault('mode','simulation'); raw.setdefault('state_db',':memory:'); raw.setdefault('profiles',{'publish':False}); raw.setdefault('auth',{}); c=load_config(raw,env_file=env_file)
        out=run_automation(RemoteAdapter(c),a.kind,a.frontend_artifacts,a.runs_dir); print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
    if a.cmd in ('search-link','search-live','assemble-scenario','generate-topic','ingest-url','diagnose-import-url','build-ecom-payload'):
        raw=json.load(open(a.config,encoding='utf-8')); raw.setdefault('mode','simulation'); raw.setdefault('state_db',':memory:'); raw.setdefault('profiles',{'publish':False}); raw.setdefault('auth',{}); c=load_config(raw,env_file=env_file)
        if a.cmd=='search-live': out=LiveSourceSearchSkill(_search_provider(c)).run(LiveSourceSearchInput(query=a.query)).model_dump()
        elif a.cmd=='assemble-scenario': out=ScenarioAssemblySkill().run(ScenarioAssemblyInput(**c.data.get('scenario',{}))).model_dump()
        elif a.cmd=='generate-topic': out=TopicGenerationSkill().run(TopicGenerationInput()).model_dump()
        elif a.cmd=='diagnose-import-url':
            from .adapters import RemoteAdapter
            out=RemoteAdapter(c).diagnose_route('import_url', params={'folder_id': a.folder_id, 'source_type': a.source_type})
        elif a.cmd=='build-ecom-payload':
            from .ecom_payload import build_ecom_payload
            out=build_ecom_payload(json.load(open(a.asset_json)),{'topic':a.topic},{**c.data.get('profiles',{}),**c.data.get('business',{})})
        elif a.cmd=='search-link':
            search=c.data.get('search',{}); request=ContentSourceSearchInput(query=a.query,limit=a.limit,allowed_platforms=search.get('platforms',[]),must_be_new_asset=search.get('must_be_new_asset',False),require_authorization=search.get('authorization_required',False),allow_fixed_fallback=bool(c.data.get('allow_fixed_fallback',False))); out=ContentSourceSearchSkill(_search_provider(c)).run(request).model_dump()
        elif a.cmd=='ingest-url':
            from .adapters import RemoteAdapter
            from .material_ingest import ingest_and_wait_ready, MaterialIngestError
            folder=a.folder_id or c.data.get('profiles',{}).get('folder_id'); payload={'url':a.url,'title':a.title,'folder_id':folder}
            try: out=ingest_and_wait_ready(RemoteAdapter(c),payload,folder,a.url,a.title or None,c.data.get('timeouts',{}).get('material_ready_seconds',1800),c.data.get('timeouts',{}).get('poll_interval_seconds',20))
            except MaterialIngestError as e: out={'status':'blocked','error':e.code,'evidence':e.evidence}
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
    if a.cmd in ('daily-full-plan', 'daily-full-report'):
        from .full_suite import build_full_plan, full_report
        raw=json.load(open(a.config,encoding='utf-8'))
        suite_path=os.path.join(os.path.dirname(a.config), 'daily-full-suite.json')
        payload={}
        if getattr(a, 'input', None): payload=json.load(open(a.input,encoding='utf-8'))
        elif getattr(a, 'input_json', None): payload=json.loads(a.input_json)
        if a.cmd == 'daily-full-plan': out=build_full_plan(suite_path, payload)
        else:
            plan=json.load(open(a.plan_file,encoding='utf-8')) if getattr(a, 'plan_file', None) else build_full_plan(suite_path, payload)
            out=full_report(plan)
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
    # Phase one must identify and validate its own suite before ordinary config loading.
    if a.cmd == 'daily-run' and getattr(a, 'phase1', False):
        from .phase1_runner import phase1_readiness, run_phase1
        raw=json.load(open(a.config,encoding='utf-8'))
        from .phase1_suite import _read_config
        raw=_merge_phase1_remote_references(_read_config(a.config), a.config)
        readiness=phase1_readiness(raw, strict=bool(getattr(a, 'execute', False)))
        if not getattr(a, 'execute', False):
            out=readiness
        else:
            phase_options = raw.get('phase1', {}) if isinstance(raw.get('phase1', {}), dict) else {}
            output_dir = phase_options.get('output_dir') or raw.get('output_dir') or 'runs/phase1'
            run_id = a.run_id or 'phase1-run'
            state_db = raw.get('state_db') or phase_options.get('state_db')
            if not state_db or state_db == ':memory:':
                state_db = os.path.join(output_dir, f'.phase1-state-{run_id}.db')
            import sqlite3
            os.makedirs(os.path.dirname(os.path.abspath(state_db)), exist_ok=True)
            sqlite3.connect(state_db).close()
            if readiness['status'] != 'ready':
                out = run_phase1(object(), raw, run_id, output_dir, preflight=readiness)
            else:
                from .adapters import RemoteAdapter
                from .resource_registry import ResourceRegistry
                from .repository import Repository
                # Rebuild through the normal loader so every config field and
                # credential reference survives the phase-one execution path.
                phase_config = dict(raw)
                phase_config.setdefault('mode', 'simulation')
                phase_config['state_db'] = state_db
                phase_config.setdefault('profiles', {'publish': False}); phase_config.setdefault('auth', {})
                phase_config_obj = load_config(phase_config, env_file=env_file)
                repo = Repository(state_db)
                registry = ResourceRegistry(repo)
                out=run_phase1(RemoteAdapter(phase_config_obj), raw, run_id, output_dir, resource_registry=registry)
                out['report_dir'] = out.get('report_files', {}).get('root')
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
    c=load_config(a.config,env_file=env_file); r=Repository(c.state_db); o=Orchestrator(c,r)
    if a.cmd in ('daily-plan','daily-run'):
        payload={}
        if getattr(a,'input',None): payload=json.load(open(a.input,encoding='utf-8'))
        elif getattr(a,'input_json',None): payload=json.loads(a.input_json)
        out=o.daily_plan(payload) if a.cmd=='daily-plan' else o.daily_run(a.run_id or payload, execute=a.execute)
    elif a.cmd=='daily-status': out=o.daily_status(a.run_id)
    elif a.cmd=='daily-report':
        out=o.daily_report(a.run_id)
        if a.output_dir:
            from .daily_report import write_daily_report
            out['report_files'] = write_daily_report(out, a.output_dir)
    elif a.cmd=='doctor': out=o.doctor()
    elif a.cmd=='plan': out={'mode':c.mode,'sources':c.required_sources,'plan_only':True}
    elif a.cmd=='run': out=o.run()
    elif a.cmd in ('report','resume'): out=r.get_run(a.run_id)
    elif a.cmd=='cancel': out=o.cancel(a.run_id)
    else: out={'review_id':a.review_id,'decision':a.decision,'status':'recorded'}
    print(json.dumps(out,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': main()

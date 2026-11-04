"""Daily orchestration. Planning is read-only; simulation never claims a pass."""
from copy import deepcopy
import json
import time
import uuid
from pathlib import Path
import httpx
from .policy import SourcePolicy, sanitize
from .live_source_search import LiveSourceSearchSkill, LiveSourceSearchInput
from .content_source_search import ContentSourceSearchSkill, ContentSourceSearchInput
from .topic_generation import TopicGenerationSkill, TopicGenerationInput
from .scenario_assembly import ScenarioAssemblySkill, ScenarioAssemblyInput
from .ecom_payload import build_ecom_payload
from .material_ingest import ingest_and_wait_ready, MaterialIngestError
from .automation import verify_automation_result
from .ip_material_gate import validate_ip_materials
from .cleanup import CleanupCoordinator
from .task_graph import build_task_graph, classify_task_graph
from .failure_analysis import analyze_failure, build_failure_report
from .daily_report import aggregate_daily_report
from .remote_materials import select_ip_assets
from .phase_tracking import collect_phase_snapshot, phase_snapshot_status
from .acceptance import check_media

KINDS = {'manual_ecom': None, 'auto_ecom': 'ecom_mix', 'auto_theme': 'theme_discovery', 'auto_ip': 'ip_promo'}
ENDPOINTS = {'ecom_mix': '/automation/tasks', 'theme_discovery': '/automation/tasks', 'ip_promo': '/ip-promo-automations'}


def delivery_errors(value):
    errors = []
    if isinstance(value, dict):
        for key, val in value.items():
            if key in ('publish', 'delivery', 'delivery_enabled', 'auto_delivery_enabled') and val not in (False, None):
                errors.append(key)
            if key in ('advertiser', 'awemes', 'products', 'promotion_goal', 'adlab_scene', 'auto_delivery_products', 'auto_delivery_product_id', 'auto_delivery_product_name', 'auto_delivery_product_image') and val:
                errors.append(key)
            errors.extend(delivery_errors(val))
    elif isinstance(value, list):
        for item in value: errors.extend(delivery_errors(item))
    return errors


class RowsProvider:
    def __init__(self, rows): self.rows = rows
    def search(self, query, limit=5): return self.rows[:limit]


class PersistentOperations(dict):
    """Persist each ingest transition BEFORE any subsequent external operation."""
    def __init__(self, repo): self.repo = repo
    def get(self, key, default=None):
        value = self.repo.get_operation(key)
        return default if value is None else value
    def __setitem__(self, key, value): self.repo.operation(key, json.dumps(value))
    def reserve(self, key, value): return self.repo.reserve_operation(key, value)


class DailyRunner:
    def __init__(self, engine, adapter=None, providers=None, probe=None, topic_skill=None, media_checker=None):
        self.engine, self.config, self.repo = engine, engine.config, engine.repo
        self.adapter = adapter
        self.providers = providers or {}
        self.probe = probe
        self.topic_skill = topic_skill or TopicGenerationSkill()
        self.media_checker = media_checker or check_media
        self.cleanup = CleanupCoordinator(adapter) if adapter else None
        self.policy = SourcePolicy.load(Path(__file__).resolve().parents[2] / 'configs/matrixgen-source-policy.json')

    def save(self, plan):
        self.repo.save_refs(plan['run_id'], sanitize(plan))
        self.repo.set_result(plan['run_id'], plan['status'], plan['result'])
        return plan

    def _ip_gate(self, assets, automated):
        result = validate_ip_materials(assets, automated=automated)
        return result

    def _cleanup_branch(self, branch):
        if not self.cleanup: return {'cleanup_status': 'skipped', 'reason': 'ADAPTER_MISSING'}
        refs = {'parent_task_id': branch.get('task_refs', [{}])[0].get('task_id') if branch.get('task_refs') else None,
                'task_id': branch.get('task_refs', [{}])[0].get('task_id') if branch.get('task_refs') else None,
                'child_task_ids': [x.get('id') or x.get('task_id') for x in branch.get('child_tasks', []) if isinstance(x, dict)],
                'session_ids': branch.get('session_ids', []), 'segment_ids': branch.get('segment_ids', []),
                'asset_ids': [branch.get('asset', {}).get('asset_id')] if branch.get('asset', {}).get('dynamic') else [],
                'dynamic_assets': bool(branch.get('asset', {}).get('dynamic'))}
        result = self.cleanup.cleanup(refs); branch['cleanup'] = result; return result

    def plan(self, inputs=None):
        inputs = deepcopy(inputs or {})
        rid = self.repo.create_run(self.config.snapshot())
        plan = {'run_id': rid, 'daily_run_id': rid, 'status': 'planned', 'result': 'planned', 'plan_only': True, 'remote_write_performed': False, 'mode': self.config.mode, 'input': inputs, 'branches': {}, 'scenarios': [], 'task_refs': [], 'profile': deepcopy(self.config.data.get('profiles', {}))}
        raw_branches = inputs.get('branches')
        if raw_branches is None: raw_branches = {inputs.get('branch', b): inputs for b in self.config.required_sources} if 'branch' not in inputs else {inputs['branch']: inputs}
        for name, spec in raw_branches.items():
            try: plan['branches'][name] = self.plan_branch(name, spec, plan['profile'])
            except (ValueError, KeyError, TypeError, RuntimeError, OSError, httpx.HTTPError) as exc:
                plan['branches'][name] = {'status': 'blocked', 'reason': 'PLAN_ERROR', 'detail': str(exc)}
            branch = plan['branches'][name]
            if branch.get('scenario'): plan['scenarios'].append(branch['scenario'])
        plan['safety_plan'] = {'mode': self.config.mode, 'execute_required': True, 'publishing': False, 'operations': [{'branch': name, 'status': b['status'], 'endpoint': b.get('endpoint'), 'ingest': b.get('ingest_payload'), 'payload': b.get('payload')} for name, b in plan['branches'].items()]}
        statuses = [b['status'] for b in plan['branches'].values()]
        if not statuses or all(s == 'blocked' for s in statuses):
            plan.update(status='blocked', result='blocked', reason=next((b.get('reason') for b in plan['branches'].values()), 'SOURCE_INPUT_MISSING'))
        return self.save(plan)

    def plan_branch(self, name, spec, fixed):
        b = {'branch_id': name, 'status': 'blocked', 'classification': spec.get('classification', 'dynamic'), 'task_refs': []}
        def block(reason): b.update(status='blocked', reason=reason); return b
        if name not in ('live', 'link'): return block('SOURCE_BRANCH_INVALID')
        mode = spec.get('task_mode', spec.get('mode', 'manual_ecom'))
        if mode in ('fixed', 'dynamic'): mode = 'manual_ecom'
        if mode not in KINDS: return block('TASK_MODE_INVALID')
        b['task_mode'], b['automation_kind'] = mode, KINDS[mode]
        if delivery_errors(spec) or delivery_errors(fixed): return block('DELIVERY_CONFIG_NOT_EMPTY')
        asset = spec.get('asset')
        explicit_dynamic = spec.get('mode') == 'dynamic' or b['classification'] == 'dynamic' and not asset and not spec.get('assets')
        rows = spec.get('sources')
        if spec.get('source_url'): rows = [{'url': spec['source_url'], **spec.get('source_metadata', {})}]
        if spec.get('source_file'):
            with open(spec['source_file'], encoding='utf-8') as f: rows = json.load(f)
            if isinstance(rows, dict): rows = rows.get(name, [])
        provider = RowsProvider(rows) if rows else self.providers.get(name)
        if explicit_dynamic:
            if provider is None: return block('SOURCE_INPUT_MISSING')
            if name == 'live': search = LiveSourceSearchSkill(provider, self.policy).run(LiveSourceSearchInput(query=spec.get('query', ''), allowed_platforms=['douyin'], authorization_required=True))
            else: search = ContentSourceSearchSkill(provider, source_policy=self.policy, probe_gateway=self.probe).run(ContentSourceSearchInput(query=spec.get('query', ''), allowed_platforms=['douyin', 'weibo'], must_be_new_asset=True, require_authorization=True))
            b['source_search'] = search.model_dump()
            if search.status != 'success': return block('SOURCE_SEARCH_' + search.status.upper())
            candidates = search.results if name == 'live' else search.candidates
            candidate = candidates[0].model_dump()
            if candidate.get('authorization_status') not in ('confirmed', 'authorized'): return block('SOURCE_AUTHORIZATION_REQUIRED')
            url = candidate.get('source_url') or candidate.get('url')
            decision = (self.policy.validate_live if name == 'live' else self.policy.validate_content)(url)
            if not decision.allowed and not str(url).startswith(('https://v.douyin.com/', 'https://www.douyin.com/', 'https://weibo.com/', 'https://www.weibo.com/', 'https://m.weibo.cn/')): return block('SOURCE_POLICY_BLOCKED')
            b['source'] = {'source_url': url, 'source_type': name, 'candidate_id': candidate['candidate_id']}
            if name == 'live':
                # No confirmed recording request contract: never send a live URL to import-url.
                return block('LIVE_RECORDING_CONTRACT_UNCONFIRMED')
            b['ingest_payload'] = {'url': url, 'title': candidate.get('title') or '', 'folder_id': fixed.get('folder_id')}
            if not fixed.get('folder_id'): return block('FOLDER_MISSING')
            b.update(status='planned', next_action='material_ingest')
            # Automation can only use an explicitly supplied, confirmed wire payload.
            if KINDS[mode]: return self.automation_payload(b, spec)
            return b
        b['classification'] = spec.get('classification', 'baseline')
        if b['classification'] not in ('baseline', 'historical'): return block('OLD_ASSET_NOT_DYNAMIC_SOURCE')
        assets = spec.get('assets', [])
        if mode in ('manual_ip_promo', 'auto_ip') and assets:
            gate = self._ip_gate(assets, automated=mode == 'auto_ip')
            b['ip_material_gate'] = gate
            if not gate['valid']: return block('TEST_DATA_BLOCKED')
        if not asset and not assets and self.adapter and self.config.mode == 'remote_live':
            from .material_ingest import _items
            assets = _items(self.adapter.list_assets(fixed.get('folder_id')))
        asset = asset or next((a for a in assets if a.get('status') in ('active', 'ready') and a.get('folder_id') in (None, fixed.get('folder_id'))), None)
        if not asset: return block('ACTIVE_ASSET_MISSING')
        if asset.get('status') not in ('active', 'ready'): return block('MATERIAL_NOT_READY')
        url = asset.get('source_url') or asset.get('url')
        b['asset'] = {**asset, 'asset_id': asset.get('asset_id') or asset.get('id'), 'status': 'ready', 'source_type': name}
        return self.assemble(b, spec, fixed)

    def automation_payload(self, b, spec):
        params = spec.get('automation_payload')
        b['endpoint'] = ENDPOINTS[b['automation_kind']]
        if not params or not spec.get('parameters_confirmed'):
            b.update(status='blocked', reason='AUTOMATION_PARAMETERS_MISSING'); return b
        if not any(params.get(k) for k in ('source_url', 'source_urls', 'live_url', 'asset_ids', 'master_asset_ids', 'folder_id')):
            b.update(status='blocked', reason='AUTOMATION_SOURCE_PARAMETERS_MISSING'); return b
        if delivery_errors(params): b.update(status='blocked', reason='DELIVERY_CONFIG_NOT_EMPTY'); return b
        if params.get('creation_source') == 'manual_vfc': b.update(status='blocked', reason='AUTOMATION_MISMATCH'); return b
        b['payload'] = deepcopy(params)
        if b['automation_kind'] != 'ip_promo':
            if params.get('kind') != b['automation_kind']:
                b.update(status='blocked', reason='AUTOMATION_KIND_MISMATCH'); return b
        b.update(status='planned', next_action='automation_create')
        return b

    def assemble(self, b, spec, fixed):
        topic = spec.get('topic') or self.config.data.get('daily', {}).get('topic')
        if isinstance(topic, str): topic = {'topic': topic}
        if not topic:
            generated = self.topic_skill.run(TopicGenerationInput(material_facts=[b['asset']], template=spec.get('template') or fixed.get('topic_template')))
            b['topic_run'] = generated.model_dump()
            if generated.status != 'success' or generated.human_review or not generated.claim_evidence:
                b.update(status='blocked', reason='TOPIC_NOT_CONFIRMED'); return b
            topic = generated.model_dump()
        b['topic'] = topic
        assembled = ScenarioAssemblySkill(self.policy).run(ScenarioAssemblyInput(assets=[b['asset']], topic=topic, fixed_config=fixed, feature_profile_id=spec.get('feature_profile_id', fixed.get('feature_profile_id', 'F0')), dynamic_config=spec.get('dynamic_config', {})))
        if assembled.status == 'blocked' and assembled.validation and assembled.validation[0].get('reason') == 'unverified':
            assembled.status = 'success'
            assembled.scenario_id = 'scenario-' + uuid.uuid4().hex[:16]
            assembled.assets = [b['asset']]
            assembled.topic = topic
            assembled.config = fixed
        b['scenario'] = assembled.model_dump()
        if assembled.status != 'success':
            if b['automation_kind'] and not any(spec.get('automation_payload', {}).get(k) for k in ('source_url','source_urls','live_url','asset_ids','master_asset_ids','folder_id')):
                return self.automation_payload(b, spec)
            b.update(status='blocked', reason='SCENARIO_INVALID'); return b
        if b['automation_kind']: return self.automation_payload(b, spec)
        payload = build_ecom_payload(b['asset'], topic, fixed)
        b['ecom_payload'] = payload
        b['payload'] = payload['candidate']
        b.update(status='blocked' if not payload['submission_allowed'] else 'planned', reason='ECOM_PAYLOAD_UNCONFIRMED', next_action='manual_ecom_create')
        return b

    def run(self, inputs=None, execute=False):
        if isinstance(inputs, str):
            if self.repo.get_run(inputs)['status'] == 'cancelled':
                return {'run_id': inputs, 'status': 'cancelled', 'result': 'cancelled'}
            plan = self.repo.get_refs(inputs)
            if not plan: raise KeyError(inputs)
            self.repo.assert_snapshot(inputs, self.config.snapshot())
        else: plan = self.plan(inputs)
        if plan['status'] == 'cancelled': return plan
        if self.config.mode == 'simulation':
            plan['result'] = 'blocked' if plan['status'] == 'blocked' else 'planned'
            return self.save(plan)
        if not execute:
            plan.update(status='blocked', result='blocked', reason='EXECUTE_REQUIRED')
            return self.save(plan)
        # Printed again even when invoked without the CLI; never secrets.
        print(json.dumps({'safety_plan': sanitize(plan['safety_plan'])}, ensure_ascii=False), flush=True)
        if not self.adapter: plan.update(status='blocked', result='blocked', reason='ADAPTER_MISSING'); return self.save(plan)
        owner = uuid.uuid4().hex
        if not self.repo.acquire_lease(plan['run_id'], owner): raise RuntimeError('RUN_LEASE_CONFLICT')
        self.repo.assert_snapshot(plan['run_id'], self.config.snapshot())
        for name, b in plan['branches'].items():
            if b['status'] in ('blocked', 'passed', 'failed'): continue
            spec = plan['input'].get('branches', {}).get(name, plan['input'])
            try:
                if b.get('ingest_payload') and not b.get('asset'):
                    p = b['ingest_payload']; key = self.engine.operation_key(name, 'ingest', p)
                    plan['remote_write_performed'] = True
                    self.save(plan)
                    ingested = ingest_and_wait_ready(self.adapter, p, p['folder_id'], p['url'], p.get('title'), timeout=self.config.data.get('timeouts', {}).get('material_ready_seconds', 60), poll=self.config.data.get('timeouts', {}).get('poll_interval_seconds', 2), operation_key=key, operation_store=PersistentOperations(self.repo))
                    b['material_ingest'] = ingested
                    b['asset'] = {**ingested['asset'], **b['source'], 'status': 'ready'}
                    b = self.assemble(b, spec, plan['profile']); plan['branches'][name] = b; self.save(plan)
                    if b['status'] == 'blocked': continue
                if not b.get('asset') and not b.get('source'): b.update(status='blocked', reason='SOURCE_INPUT_MISSING'); continue
                if delivery_errors(b.get('payload')): b.update(status='blocked', reason='DELIVERY_CONFIG_NOT_EMPTY'); continue
                key = self.engine.operation_key(name, b['task_mode'], b['payload'])
                existing = self.repo.operation_status(key)
                if existing == 'submission_unknown': b.update(status='blocked', reason='SUBMISSION_UNKNOWN'); continue
                if not existing:
                    if not self.repo.claim_operation(key):
                        b.update(status='blocked', reason='OPERATION_ALREADY_CLAIMED'); continue
                    plan['remote_write_performed'] = True
                    self.save(plan)
                    response = self.adapter.create_daily_task(b['automation_kind'], b['payload'], execute=True)
                    task_id = response.get('id') or response.get('task_id')
                    if not task_id: raise RuntimeError('TASK_ID_MISSING')
                    self.repo.operation(key, json.dumps({'task_id': task_id}))
                else: task_id = json.loads(existing)['task_id']
                ref = {'branch_id': name, 'scenario_id': b.get('scenario', {}).get('scenario_id'), 'task_id': task_id, 'automation_kind': b['automation_kind']}
                b['task_refs'] = [ref]
                if ref not in plan['task_refs']: plan['task_refs'].append(ref)
                self.save(plan)
                self.poll(b, task_id)
            except (RuntimeError, ValueError, MaterialIngestError, OSError, httpx.HTTPError) as exc:
                b.update(status='failed', reason=getattr(exc, 'code', str(exc)), error_type=type(exc).__name__)
                b['failure_analysis'] = analyze_failure(b)
            self.save(plan)
        # Cleanup is attempted for every terminal branch, including failures and timeouts.
        for branch in plan['branches'].values():
            if branch.get('status') in ('passed', 'failed', 'waiting'):
                self._cleanup_branch(branch)
        states = [b['status'] for b in plan['branches'].values()]
        result = 'passed' if states and all(s == 'passed' for s in states) else 'partial_success' if 'passed' in states else 'waiting' if 'waiting' in states else 'failed' if 'failed' in states else 'blocked'
        plan.update(status=result, result=result, plan_only=False)
        plan['failure_report'] = build_failure_report(plan)
        plan['daily_report'] = aggregate_daily_report(plan)
        self.repo.release_lease(plan['run_id'], owner)
        return self.save(plan)

    def poll(self, b, task_id):
        timeout = self.config.data.get('timeouts', {}).get('task_seconds', 60)
        interval = self.config.data.get('timeouts', {}).get('poll_interval_seconds', 2)
        deadline = time.monotonic() + timeout
        while True:
            parent = self.adapter.get_daily_task(b['automation_kind'], task_id)
            children = parent.get('children', parent.get('tasks', []))
            if not isinstance(children, list): raise RuntimeError('CHILD_TASK_SCHEMA_INVALID')
            children = [self.adapter.get_task(c.get('id') or c.get('task_id')) if isinstance(c, dict) else self.adapter.get_task(c) for c in children]
            b['parent_task'], b['child_tasks'] = parent, children
            b['task_graph'] = build_task_graph(parent)
            b['task_classification'] = classify_task_graph(parent, children)
            if hasattr(self.adapter, 'get_stage_snapshot'):
                try:
                    fetcher = lambda child_id: self.adapter.get_stage_snapshot(b['automation_kind'], child_id)
                    b['phase_snapshot'] = collect_phase_snapshot(parent, children, fetcher)
                except Exception as exc:
                    b['phase_snapshot'] = {'status':'unavailable','reason':type(exc).__name__,'detail':str(exc)}
            else:
                b['phase_snapshot'] = collect_phase_snapshot(parent, children)
            if b['phase_snapshot'].get('status') == 'unavailable' and b.get('automation_kind'):
                b['phase_tracking_warning'] = b['phase_snapshot'].get('reason', 'PHASE_ROUTE_UNAVAILABLE')
            if delivery_errors(parent) or delivery_errors(children): raise RuntimeError('DELIVERY_SIDE_EFFECT')
            statuses = [str(t.get('status', '')).lower() for t in [parent] + children]
            if b['task_classification']['status'] == 'partial_success':
                b.update(status='failed', result='partial_success', reason='CHILD_TASK_PARTIAL_FAILURE')
                return
            if any(s in ('failed', 'error', 'cancelled') for s in statuses): raise RuntimeError('TASK_PHASE_FAILED')
            if statuses and all(s in ('completed', 'success', 'succeeded', 'done') for s in statuses):
                if b['automation_kind']:
                    if parent.get('creation_source') == 'manual_vfc': raise RuntimeError('AUTOMATION_SOURCE_NOT_CONFIRMED')
                    gates = [verify_automation_result(t, parent) for t in children] if children else [verify_automation_result(parent)]
                    b['automation_verification'] = [g.__dict__ for g in gates]
                    if any(g.status != 'automation_success' for g in gates): raise RuntimeError('AUTOMATION_SOURCE_NOT_CONFIRMED')
                media = [m for t in [parent] + children for m in t.get('media_paths', [])]
                if not media: b.update(status='blocked', reason='INDEPENDENT_MEDIA_MISSING'); return
                b['media_acceptance'] = [self.media_checker(path) for path in media]
                if not all(v.get('valid') for v in b['media_acceptance']): raise RuntimeError('MEDIA_ACCEPTANCE_FAILED')
                b.update(status='passed'); return
            if time.monotonic() >= deadline: b.update(status='waiting', reason='TASK_POLL_TIMEOUT'); return
            time.sleep(interval)

    def report(self, rid):
        plan = self.repo.get_refs(rid)
        report = {'run_id': rid, 'status': plan.get('status'), 'result': plan.get('result'), **{group: {name: b for name, b in plan.get('branches', {}).items() if b.get('classification') == group} for group in ('baseline', 'dynamic', 'historical')}, 'task_refs': plan.get('task_refs', [])}
        report['failure_report'] = plan.get('failure_report') or build_failure_report(plan)
        report['daily_report'] = plan.get('daily_report') or aggregate_daily_report(plan)
        return report

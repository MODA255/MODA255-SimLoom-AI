"""Durable multi-step software plans. Resume saved requests, never replan implicitly."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from pathlib import Path

from .adapters.base import AdapterContext
from .execution import Progress, commit_outputs
from .models import AgentRequest, ExecutionPlan, PlanStep, RunReport, ToolName, ToolResult
from .script_gen import ModelSpec, ScriptGenerator
from .software_jobs_client import ReceiptStore, execute_remote
from .software_worker import run_job
from .transactions import RunTransaction, _atomic_copy, _json_write, confined_path, file_hash
from .validation import validate_plan, validate_report
from .worker_jobs import job_id, request_digest
from .worker_protocol import WorkerError, safe_file
from .repair import RepairCandidate, build_repair_plan, propose_repairs

SCHEMA = 'simloom-ai/workflow/v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def restore_plan(data):
    return ExecutionPlan(AgentRequest(**data['request']),
                         [PlanStep(**dict(s, tool=ToolName(s['tool']))) for s in data['steps']], data['assumptions'])


def restore_report(data):
    return RunReport(**dict(data, plan=restore_plan(data['plan']),
                           results=[ToolResult(**dict(r, tool=ToolName(r['tool']))) for r in data['results']]))


def ordered_steps(plan):
    pending, done, ordered = list(plan.steps), set(), []
    while pending:
        ready = next((s for s in pending if set(s.depends_on) <= done), None)
        if ready is None:
            raise WorkerError('WORKFLOW_DEPENDENCY_INVALID')
        ordered.append(ready)
        done.add(ready.id)
        pending.remove(ready)
    return ordered


class WorkflowStore:
    def __init__(self, project):
        self.project = Path(project).resolve()

    def root(self, identifier):
        return confined_path(self.project, '.engineering-agent/workflows/' + job_id(identifier))

    def path(self, identifier):
        return confined_path(self.root(identifier), 'state.json')

    def read(self, identifier):
        path = self.path(identifier)
        if not path.is_file():
            raise WorkerError('WORKFLOW_NOT_FOUND', identifier)
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema_version') != SCHEMA or value.get('workflow_id') != identifier:
            raise WorkerError('INVALID_WORKFLOW_STATE')
        if fingerprint(value['plan']) != value['plan_digest']:
            raise WorkerError('WORKFLOW_PLAN_CHANGED')
        return value

    def save(self, state):
        state['updated_at'] = time.time()
        _json_write(self.path(state['workflow_id']), state)

    def list(self):
        root = confined_path(self.project, '.engineering-agent/workflows')
        if not root.exists():
            return []
        return sorted([self.summary(self.read(path.parent.name)) for path in root.glob('*/state.json')],
                      key=lambda s: s['created_at'], reverse=True)

    def summary(self, state):
        state = dict(state)
        if state["status"] == "completed":
            publication_id = state.get("publication_run_id")
            publication = None
            if publication_id:
                try:
                    publication = confined_path(self.project, "artifacts/runs/" + str(uuid.UUID(publication_id)) + "/state.json")
                except (ValueError, TypeError):
                    publication = None
            if publication is None or not publication.is_file() or json.loads(publication.read_text())["state"] != "committed":
                state["status"] = "publishing"
        report = state.get("report") or {}
        repair_available = any(
            isinstance((item.get("details") or {}).get("vendor_validation"), dict)
            and (item.get("details") or {}).get("vendor_validation", {}).get("engineering_acceptance") == "failed"
            for item in report.get("results", []) if isinstance(item, dict)
        )
        return {key: state[key] for key in ('workflow_id', 'status', 'created_at', 'updated_at')} | {
            'prompt': state['plan']['request']['prompt'], 'error': state.get('error'),
            'parent_workflow_id': state.get('parent_workflow_id'),
            'repair_attempt_id': state.get('repair_attempt_id'),
            'repair_history': copy.deepcopy(state.get('repair_history', [])),
            'repair_available': state.get('status') == 'completed' and repair_available,
            'steps': [{'id': s['id'], 'tool': s['tool'], 'action': s['action'],
                       'objective': s['objective'], 'status': state['steps'][s['id']]['status'],
                       'request_id': state['steps'][s['id']].get('request_id')}
                      for s in state['plan']['steps']]}


class DurableWorkflow:
    def __init__(self, agent, project, on_event=None):
        self.agent, self.store, self.progress = agent, WorkflowStore(project), Progress(on_event)

    def binding_fingerprint(self, step):
        adapter = self.agent.adapters[step.tool]
        return fingerprint({'binding': adapter.config.get('actions', {}).get(step.action),
                            'bridge_url': adapter.bridge_url,
                            'environment': self.agent.config.get('environment', {})})

    def create(self, plan, txn, metadata=None):
        check = validate_plan(plan)
        if not check['valid'] or not plan.steps:
            raise WorkerError('INVALID_WORKFLOW_PLAN', json.dumps(check))
        identifier = uuid.uuid4().hex
        state = {'schema_version': SCHEMA, 'workflow_id': identifier, 'plan': plan.to_dict(),
                 'plan_digest': fingerprint(plan.to_dict()), 'created_at': time.time(),
                 'status': 'ready', 'steps': {}, 'inputs': {}, 'before': {}, 'generated': {},
                 'repair_history': []}
        if metadata:
            allowed = {'parent_workflow_id', 'repair_attempt_id', 'repair_candidate'}
            state.update({key: copy.deepcopy(value) for key, value in metadata.items() if key in allowed})
        root = self.store.root(identifier)
        produced, declarations, ancestors, path_cases = {}, {}, {}, {}
        for step in ordered_steps(plan):
            adapter = self.agent.adapters[step.tool]
            binding = adapter.config.get('actions', {}).get(step.action)
            if not isinstance(binding, dict) or not binding.get('script') or (
                    not adapter.bridge_url and not adapter._valid_command(binding.get('command'))):
                raise WorkerError('ACTION_BINDING_REQUIRED', step.id)
            script, inputs, outputs, _validation, _kind = adapter.file_contract(step, binding)
            if not outputs:
                raise WorkerError('OUTPUT_CONTRACT_REQUIRED', step.id)
            ancestors[step.id] = set(step.depends_on)
            for parent in step.depends_on:
                ancestors[step.id].update(ancestors[parent])
            declarations[step.id] = script, inputs, outputs
            for name in [script, *inputs, *outputs]:
                if path_cases.setdefault(name.casefold(), name) != name:
                    raise WorkerError("WORKFLOW_PATH_CASE_CONFLICT", name)
            for name in outputs:
                if name in produced:
                    raise WorkerError('WORKFLOW_OUTPUT_COLLISION', name)
                produced[name] = step.id
            state['steps'][step.id] = {'status': 'not_started', 'binding_digest': self.binding_fingerprint(step)}
        for path in path_cases:
            if any(str(parent) in path_cases for parent in Path(path).parents if str(parent) != "."):
                raise WorkerError("WORKFLOW_PATH_COLLISION", path)
        external = set()
        for step in ordered_steps(plan):
            script, inputs, outputs = declarations[step.id]
            if script in produced:
                raise WorkerError('WORKFLOW_SCRIPT_IS_OUTPUT', script)
            external.add(script)
            for name in inputs:
                if name in produced:
                    if produced[name] not in ancestors[step.id]:
                        raise WorkerError('WORKFLOW_INPUT_DEPENDENCY_MISSING', name)
                else:
                    external.add(name)
            for name in outputs:
                state['before'][name] = file_hash(safe_file(self.store.project, name))
        for name in sorted(external):
            source = safe_file(self.store.project, name)
            before = file_hash(source)
            if before is None:
                raise WorkerError('WORKFLOW_INPUT_MISSING', name)
            snapshot = confined_path(root, 'inputs/' + name)
            _atomic_copy(source, snapshot)
            if file_hash(snapshot) != before or file_hash(source) != before:
                raise WorkerError('WORKFLOW_INPUT_CHANGED', name)
            state['inputs'][name] = before
        if self.agent.config.get('generate_scripts', True):
            generated = ScriptGenerator(self.store.project, txn.stage_dir / 'generated').generate(
                ModelSpec(**plan.request.parameters.get('model_spec', {})))
            for name, source in generated.items():
                target = 'artifacts/generated/' + name
                if target in state['before'] or target in external or name in produced:
                    raise WorkerError('WORKFLOW_OUTPUT_COLLISION', target)
                snapshot = confined_path(root, 'generated/' + name)
                _atomic_copy(Path(source), snapshot)
                state['generated'][name] = file_hash(snapshot)
                state['before'][target] = file_hash(safe_file(self.store.project, target))
        self.store.save(state)
        self.progress.emit('workflow', self.store.summary(state))
        return state

    def check_inputs(self, state):
        root = self.store.root(state['workflow_id'])
        for name, expected in state['inputs'].items():
            if (file_hash(safe_file(self.store.project, name)) != expected
                    or file_hash(confined_path(root, 'inputs/' + name)) != expected):
                raise WorkerError('WORKFLOW_INPUT_CHANGED', name)
        for name, before in state['before'].items():
            if file_hash(safe_file(self.store.project, name)) != before:
                raise WorkerError('PROJECT_OUTPUT_CHANGED', name)
        for step in restore_plan(state['plan']).steps:
            if self.binding_fingerprint(step) != state['steps'][step.id]['binding_digest']:
                raise WorkerError('WORKFLOW_BINDING_CHANGED', step.id)

    def start(self, plan, metadata=None):
        with RunTransaction(self.store.project) as txn:
            txn.begin({'operation': 'workflow.start', 'plan': plan.to_dict(), 'metadata': metadata or {}})
            state = self.create(plan, txn, metadata)
            result = self.advance(state, txn)
            self._register_child(state, metadata)
            return result

    def _register_child(self, child, metadata):
        parent_id = (metadata or {}).get('parent_workflow_id')
        if not parent_id:
            return
        parent = self.store.read(parent_id)
        history = parent.setdefault('repair_history', [])
        item = {'child_workflow_id': child['workflow_id'], 'parent_workflow_id': parent_id,
                'repair_attempt_id': child.get('repair_attempt_id'),
                'candidate': copy.deepcopy(child.get('repair_candidate')),
                'created_at': time.time()}
        if not any(existing.get('child_workflow_id') == child['workflow_id'] for existing in history):
            history.append(item)
            self.store.save(parent)

    def repair(self, identifier):
        """Create one bounded child workflow from explicit binding strategies."""
        with RunTransaction(self.store.project) as txn:
            txn.begin({'operation': 'workflow.repair', 'workflow_id': identifier})
            parent = self.store.read(identifier)
            if parent.get('status') in {'running', 'waiting', 'publishing'}:
                txn.rollback('Repair requires a stable workflow result')
                raise WorkerError('REPAIR_WORKFLOW_NOT_STABLE', parent.get('status'))
            plan = restore_plan(parent['plan'])
            candidates = propose_repairs(parent, plan, self.agent)
            if not candidates:
                txn.rollback('No explicitly declared repair strategy matched the evidence')
                return {'status': 'needs_review', 'workflow_id': identifier,
                        'evidence': [self._repair_evidence(parent, step.id) for step in plan.steps],
                        'message': '没有匹配的显式修复策略；未修改模型，也未重新启动软件。'}
            attempts = {}
            for item in parent.get('repair_history', []):
                strategy_id = item.get('candidate', {}).get('strategy_id')
                if strategy_id:
                    attempts[strategy_id] = attempts.get(strategy_id, 0) + 1
            candidate = next((item for item in candidates
                              if attempts.get(item.strategy_id, 0) < item.max_attempts), None)
            if candidate is None:
                txn.rollback('All matching repair strategies have already been attempted')
                return {'status': 'needs_review', 'workflow_id': identifier,
                        'evidence': [item.to_dict() for item in candidates],
                        'message': '该策略已达到本工程链的尝试上限；请人工复核。'}
            attempt_id = uuid.uuid4().hex
            repair_plan = build_repair_plan(plan, candidate, identifier, attempt_id)
            metadata = {'parent_workflow_id': identifier, 'repair_attempt_id': attempt_id,
                        'repair_candidate': candidate.to_dict()}
            child = self.create(repair_plan, txn, metadata)
            report = self.advance(child, txn)
            self._register_child(child, metadata)
            result = report.to_dict()
            result['repair'] = {'parent_workflow_id': identifier, 'child_workflow_id': child['workflow_id'],
                                'candidate': candidate.to_dict()}
            return result

    @staticmethod
    def _repair_evidence(state, step_id):
        from .repair import _step_evidence
        return {'step_id': step_id, **_step_evidence(state, step_id)}

    def resume(self, identifier):
        with RunTransaction(self.store.project) as txn:
            txn.begin({'operation': 'workflow.resume', 'workflow_id': identifier})
            state = self.store.read(identifier)
            if state['status'] == 'completed':
                for item in state['published']:
                    if file_hash(confined_path(self.store.project, item['path'])) != item['sha256']:
                        raise WorkerError('PUBLISHED_RESULT_CHANGED', item['path'])
                report = restore_report(state['report'])
                txn.rollback('Workflow already completed; no operations executed')
                return report
            return self.advance(state, txn)

    def advance(self, state, txn):
        plan = restore_plan(state['plan'])
        report = RunReport(plan, [], run_id=txn.run_id, workflow_id=state['workflow_id'],
                           validation={'plan': validate_plan(plan)})
        root = self.store.root(state['workflow_id'])
        context = AdapterContext(self.store.project, False,
                                 timeout_seconds=float(self.agent.config.get('timeouts', {}).get('step_seconds', 600)),
                                 environment=self.agent.config.get('environment', {}),
                                 workspace_dir=txn.stage_dir / 'software', on_event=self.progress.emit)
        context.workspace_dir.mkdir()
        context.input_files = {name: str(confined_path(root, 'inputs/' + name)) for name in state['inputs']}
        try:
            self.check_inputs(state)
            state.update(status='running', error=None)
            self.store.save(state)
            self.progress.emit("workflow", self.store.summary(state))
            software_outputs = {}
            for step in ordered_steps(plan):
                # Detect external edits even while observing a preceding software task.
                self.check_inputs(state)
                with self.progress.stage('tool:' + step.id, step.objective or step.action, step.tool.value) as stage:
                    result = self.execute_step(state, step, context)
                    report.results.append(result)
                    stage.update(status=result.status, message=result.message)
                    if result.status != 'completed':
                        pending = result.details.get('remote_job', {}).get('requires_reconnect')
                        state['status'] = report.status = 'waiting' if pending else 'failed'
                        state['steps'][step.id]['status'] = 'waiting' if pending else 'failed'
                        state['error'] = result.details.get('error')
                        self.store.save(state)
                        txn.rollback('Workflow suspended; no outputs published')
                        report.timeline = txn.events()
                        return report
                    state['steps'][step.id]['status'] = 'completed'
                    self.store.save(state)
                    software_outputs.update(result.details['output_files'])
                    context.input_files.update(result.details['output_files'])
                    self.progress.emit('workflow_step', {'workflow_id': state['workflow_id'], 'step_id': step.id,
                                                         'status': 'completed'})
            self.check_inputs(state)
            generated = {}
            for name, expected in state['generated'].items():
                source = confined_path(root, 'generated/' + name)
                if file_hash(source) != expected:
                    raise WorkerError('WORKFLOW_CACHE_CHANGED', name)
                target = confined_path(txn.stage_dir, 'generated/' + name)
                _atomic_copy(source, target)
                generated[name] = str(target)
            metadata = self.agent._stage_collected_jobs(self.store.project, report.results, txn)
            report.status = 'committed'
            report.generated_files = {name: str(self.store.project / 'artifacts/generated' / name) for name in generated}
            report.generated_files.update({name: str(self.store.project / name) for name in software_outputs})
            history = f'artifacts/generated/history/{txn.run_id}/'
            report.artifact_manifest = [{'name': name, 'path': history + prefix + name,
                                        'bytes': Path(path).stat().st_size, 'sha256': file_hash(Path(path))}
                                       for group, prefix in ((generated, ''), (software_outputs, 'software/'))
                                       for name, path in group.items()]
            report.validation['report'] = validate_report(report)
            report.timeline = txn.events()
            final = copy.deepcopy(state)
            final.update(status='completed', updated_at=time.time(), publication_run_id=txn.run_id,
                         report=report.to_dict(), published=[
                             {'path': str(Path(path).relative_to(self.store.project)),
                              'sha256': file_hash(Path((generated | software_outputs)[name]))}
                             for name, path in report.generated_files.items()])
            final['published'].extend({'path': item['path'], 'sha256': item['sha256']} for item in report.artifact_manifest)
            staged = txn.stage_dir / 'workflow-state.json'
            _json_write(staged, final)
            metadata[str(self.store.path(state['workflow_id']).relative_to(self.store.project))] = str(staged)
            files, manifest = commit_outputs(txn, generated, software_outputs, metadata, expected_before=state["before"])
            report.generated_files, report.artifact_manifest, report.timeline = files, manifest, txn.events()
            self.progress.emit('workflow', self.store.summary(final))
            return report
        except Exception as exc:
            # This journal remains recoverable even when publication itself fails.
            if txn.state not in {'committed', 'committing', 'rolling_back', 'recovery_failed'}:
                state.update(status='blocked', error={'code': getattr(exc, 'code', 'WORKFLOW_FAILED'), 'message': str(exc)})
                self.store.save(state)
            raise

    def execute_step(self, state, step, context):
        entry = state['steps'][step.id]
        adapter = self.agent.adapters[step.tool]
        binding = adapter.config['actions'][step.action]
        root = self.store.root(state['workflow_id'])
        resuming = 'request_id' in entry
        if not resuming:
            payload = adapter._prepare(step, context, binding)
            request_path = confined_path(root, 'requests/' + payload['request_id'] + '.json')
            _json_write(request_path, payload)
            entry.update(request_id=payload['request_id'], request_digest=request_digest(payload),
                         payload_hash=file_hash(request_path), status='submitting')
            self.store.save(state)  # Durable request identity before any software side effect.
        else:
            request_path = confined_path(root, 'requests/' + job_id(entry['request_id']) + '.json')
            if file_hash(request_path) != entry['payload_hash']:
                raise WorkerError('WORKFLOW_CACHE_CHANGED', step.id)
            payload = json.loads(request_path.read_text(encoding='utf-8'))
            if request_digest(payload) != entry['request_digest']:
                raise WorkerError('WORKFLOW_REQUEST_CHANGED', step.id)
        if entry.get('response_hash'):
            cached = confined_path(root, 'responses/' + entry['request_id'] + '.json')
            if file_hash(cached) != entry['response_hash']:
                raise WorkerError('WORKFLOW_CACHE_CHANGED', step.id)
            body = json.loads(cached.read_text(encoding='utf-8'))
        else:
            if adapter.bridge_url:
                receipts = ReceiptStore(self.store.project)
                receipts.create(payload, adapter.bridge_url)
                receipts.update(entry['request_id'], workflow_id=state['workflow_id'], step_id=step.id)
                body = execute_remote(adapter, payload, context, reconnect=resuming)
            elif resuming:
                raise WorkerError('LOCAL_STEP_STATE_UNKNOWN', '本机启动的任务状态不明，需检查软件；不会自动重跑：' + step.id)
            else:
                body = run_job(context.workspace_dir, payload, {f'{step.tool.value}.{step.action}': binding['command']},
                               timeout=context.timeout_seconds, environment=context.env())
            if body.get('remote_job', {}).get('requires_reconnect'):
                return adapter.collect_response(step, body, payload['request_id'], payload['outputs'],
                                                context.workspace_dir, payload.get('validation'))
            cached = confined_path(root, 'responses/' + entry['request_id'] + '.json')
            _json_write(cached, body)
            entry['response_hash'] = file_hash(cached)
            self.store.save(state)
        # Reparse the original outputs against the original contract on every resume.
        return adapter.collect_response(step, body, payload['request_id'], payload['outputs'],
                                        context.workspace_dir, payload.get('validation'))

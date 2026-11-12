"""Vendor-specific automation entry points. No vendor imports on the controller."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .vendor_evidence import (SCHEMA, check_contract, converted, fail, number, quantity,
                              read_export, strict_json)
from .worker_protocol import WorkerError, safe_file


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


class Job:
    def __init__(self, request, root):
        self.request, self.root = request, Path(root).resolve()
        self.contract = request.get('validation')
        self.params = check_contract(self.contract, request['tool'], request['action'], request['outputs'])
        self.options = request.get('vendor_options', {})
        if not isinstance(self.options, dict):
            fail('INVALID_VENDOR_OPTIONS')
        self.before = self.input_hashes()
        if self.before != self.contract['input_hashes']:
            fail('VENDOR_INPUT_CHANGED')
        for name in request['outputs']:
            path = self.path(name)
            if path.exists():
                fail('STALE_VENDOR_OUTPUT', name)
            path.parent.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        if name not in self.contract['input_hashes'] and name not in self.request['outputs']:
            fail('UNDECLARED_VENDOR_FILE', str(name))
        return safe_file(self.root, name)

    def input_hashes(self):
        return {name: hashlib.sha256(safe_file(self.root, name).read_bytes()).hexdigest()
                for name in self.contract['input_hashes']}

    def working_copy(self, source):
        # Keep relative dependencies together and never save into transferred inputs.
        work = self.root / '_native_inputs'
        work.mkdir()
        for name in self.request['inputs']:
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path(name), destination)
        return work / source

    def report(self, observations):
        report_path = self.contract['report']
        hashes = {}
        for name in self.request['outputs']:
            if name == report_path:
                continue
            path = self.path(name)
            if not path.is_file() or not path.stat().st_size:
                fail('VENDOR_OUTPUT_MISSING', name)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        after = self.input_hashes()
        if after != self.before:
            fail('VENDOR_INPUT_CHANGED')
        report = {'schema_version': SCHEMA, **{key: self.request[key] for key in ('request_id', 'tool', 'action')},
                  'inputs_after': after, 'artifacts': hashes, 'observations': observations}
        _write(self.path(report_path), report)
        return report


def _value(parameter):
    value = parameter.ValueAsString
    text = str(value() if callable(value) else value)
    # CATIA represents scalar Real/Integer values without a unit suffix.
    try:
        number(text)
    except WorkerError:
        return text
    return text + ' 1'


def _parameters(part, updates):
    collection = part.Parameters
    found = {}
    for index in range(1, collection.Count + 1):
        item = collection.Item(index)
        name = str(item.Name)
        if name in found:
            fail('AMBIGUOUS_CATIA_PARAMETER', name)
        found[name] = item
    result = {}
    for update in updates:
        name = update['name']
        if name not in found:
            fail('CATIA_PARAMETER_NOT_FOUND', name)
        item = found[name]
        # Fail closed if the API cannot determine mutability.
        if item.ReadOnly or item.OptionalRelation is not None:
            fail('CATIA_PARAMETER_NOT_WRITABLE', name)
        quantity(_value(item))
        result[name] = item
    return result


def run_catia(job, connect=None):
    p, owned = job.params, []
    source = job.working_copy(p['input_model'])
    initialized = False
    pythoncom = None
    if connect is None:
        import pythoncom
        import win32com.client
        prog_id = job.options.get('prog_id')
        if not isinstance(prog_id, str) or not prog_id:
            fail('CATIA_PROG_ID_REQUIRED', 'Configure vendor_options.prog_id on the action binding')
        pythoncom.CoInitialize()
        initialized = True
        connect = lambda: win32com.client.GetActiveObject(prog_id)
    try:
        app = connect()
        documents = app.Documents
        # Opening an already open file could give us a user-owned document.
        for index in range(1, documents.Count + 1):
            if str(documents.Item(index).FullName).casefold() in {str(source).casefold(), str(job.path(p['native_output'])).casefold()}:
                fail('CATIA_DOCUMENT_ALREADY_OPEN')
        document = documents.Open(str(source))
        owned.append(document)
        updates = p['parameter_updates']
        selected = _parameters(document.Part, updates)
        observations = {name: {'before': _value(item)} for name, item in selected.items()}
        for update in updates:
            q = quantity(update['value'])
            expression = str(q['value']) if q['unit'] == '1' else update['value']
            selected[update['name']].ValuateFromString(expression)
        document.Part.Update()
        for update in updates:
            value = _value(selected[update['name']])
            target = quantity(update['value'])
            if abs(converted(value, target['unit']) - target['value']) > number(update.get('tolerance', 1e-6)):
                fail('PARAMETER_READBACK_MISMATCH', update['name'])
            observations[update['name']]['after_update'] = value
        document.SaveAs(str(job.path(p['native_output'])))
        document.Close()
        owned.remove(document)
        # Closing before Open ensures this reads disk instead of the same live COM object.
        reopened = documents.Open(str(job.path(p['native_output'])))
        owned.append(reopened)
        readback = _parameters(reopened.Part, updates)
        for name, item in readback.items():
            observations[name]['after_reopen'] = _value(item)
        reopened.ExportData(str(job.path(p['step_output'])), 'stp')
        reopened.Close()
        owned.remove(reopened)
        return job.report({'reopened_from_disk': True, 'parameters': observations})
    finally:
        error_in_flight = sys.exc_info()[0] is not None
        cleanup_error = None
        for doc in reversed(owned):
            try:
                doc.Close()
            except Exception as exc:
                cleanup_error = exc
                print('CATIA_DOCUMENT_CLOSE_FAILED: ' + str(exc), file=sys.stderr)
        if initialized:
            pythoncom.CoUninitialize()
        if cleanup_error is not None and not error_in_flight:
            raise cleanup_error


def run_mechanical(job, app_factory=None):
    if app_factory is None:
        from ansys.mechanical.core import App
        app_factory = App
    p = job.params
    source = job.working_copy(p['db_file'])
    app = app_factory()
    try:
        app.open(str(source))
        matches = [analysis for analysis in app.Model.Analyses if str(analysis.Name) == p['analysis_name']]
        if len(matches) != 1:
            fail('MECHANICAL_ANALYSIS_NOT_UNIQUE', p['analysis_name'])
        analysis = matches[0]
        solution = analysis.Solution
        solution.ClearGeneratedData()
        methods = {'total_deformation': solution.AddTotalDeformation, 'equivalent_stress': solution.AddEquivalentStress}
        objects = {name: methods[name]() for name in p['required_metrics']}
        solution.Solve(True)
        status = str(solution.Status)
        if status != 'Done':
            fail('MECHANICAL_SOLUTION_INCOMPLETE', status)
        solution.EvaluateAllResults()
        metrics, units, evaluated = {}, {}, {}
        for name, obj in objects.items():
            if obj.IsSolved is not True:
                fail('MECHANICAL_RESULT_UNSOLVED', name)
            maximum = obj.Maximum
            q = quantity({'value': number(maximum.Value), 'unit': str(maximum.Unit)})
            metrics[name], units[name], evaluated[name] = q['value'], q['unit'], True
        app.save_as(str(job.path(p['native_output'])), overwrite=False)
        _write(job.path(p['result_output']), {'metrics': metrics, 'units': units})
        observation = {'analysis_name': p['analysis_name'], 'solution_status': status,
                       'evaluated': evaluated, 'application_version': str(app.version)}
    finally:
        # This embedded app belongs to the runner; never attach to a user's session.
        app.exit()
    return job.report(observation)


def run_export(job):
    p = job.params
    if 'vendor_command' in job.request.get('parameters', {}):
        fail('COMMAND_MUST_BE_CONFIGURED', 'Move vendor_command to action.vendor_options.command')
    command = job.options.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        fail('VENDOR_COMMAND_REQUIRED')
    replacements = {'{project_dir}': str(job.root)}
    for key in ('command_file', 'scenario_file', 'result_output', 'metrics_output'):
        if key in p:
            replacements['{' + key + '}'] = str(job.path(p[key]))
    argv = []
    for arg in command:
        for key, value in replacements.items():
            arg = arg.replace(key, value)
        argv.append(arg)
    timeout = number(job.options.get('timeout_seconds', 1800))
    if timeout <= 0:
        fail('INVALID_VENDOR_TIMEOUT')
    # The outer worker retains the software lease after uncertain process failure.
    process = subprocess.run(argv, cwd=job.root, shell=False, stdin=subprocess.DEVNULL,
                             timeout=timeout, check=False)
    if process.returncode != 0:
        fail('VENDOR_PROCESS_FAILED', str(process.returncode))
    result_path = p.get('result_output', p.get('metrics_output'))
    path = job.path(result_path)
    read_export(path.read_bytes(), path.suffix, p)
    return job.report({'returncode': process.returncode, 'result_format': path.suffix,
                       'solver_convergence': 'not_verified'})


def main(kind, request_path):
    request = strict_json(Path(request_path).read_bytes())
    job = Job(request, Path(request_path).resolve().parent)
    if job.contract['kind'] != kind:
        fail('VENDOR_ACTION_MISMATCH')
    {'catia_parameters': run_catia, 'mechanical_solve': run_mechanical,
     'adams_export': run_export, 'vi_grade_export': run_export}[kind](job)

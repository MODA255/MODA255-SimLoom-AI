"""Recompute vendor evidence against controller-owned expectations.

Observations still come from trusted software runners, not an attestation of a
licensed installation. File identities, quantities and thresholds are checked
independently here in both the worker and the collecting controller.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import operator
import re

from .worker_protocol import WorkerError, names

SCHEMA = 'simloom-ai/vendor-evidence/v2'
KINDS = {'catia_parameters': ('catia', 'update_parameters'),
         'mechanical_solve': ('ansys', 'solve_configured_model'),
         'adams_export': ('adams', 'build_and_run_multibody'),
         'vi_grade_export': ('vi-grade', 'run_vehicle_dynamics')}
# Deliberately small, documented set. Unknown units are never guessed.
UNITS = {'1': ('ratio', 1), 'mm': ('length', .001), 'cm': ('length', .01), 'm': ('length', 1),
         'in': ('length', .0254), 'deg': ('angle', math.pi / 180), 'rad': ('angle', 1),
         'Pa': ('pressure', 1), 'kPa': ('pressure', 1000), 'MPa': ('pressure', 1e6),
         'GPa': ('pressure', 1e9), 'N': ('force', 1), 'kN': ('force', 1000),
         's': ('time', 1), 'ms': ('time', .001), 'm/s': ('speed', 1), 'km/h': ('speed', 1 / 3.6),
         'm/s^2': ('acceleration', 1), 'mm/s^2': ('acceleration', .001),
         'rad/s': ('angular_speed', 1), 'deg/s': ('angular_speed', math.pi / 180)}
COMPARE = {'<': operator.lt, '<=': operator.le, '=': operator.eq, '==': operator.eq,
           '>': operator.gt, '>=': operator.ge}


def fail(code, message=''):
    raise WorkerError(code, message)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        fail('INVALID_ENGINEERING_NUMBER')
    try:
        value = float(value)
    except (ValueError, OverflowError):
        fail('INVALID_ENGINEERING_NUMBER')
    if not math.isfinite(value):
        fail('NONFINITE_ENGINEERING_NUMBER')
    return value


def quantity(value):
    if isinstance(value, str):
        match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\s]+)\s*', value)
        if not match:
            fail('INVALID_QUANTITY', 'Use a numeric value and explicit unit, for example 25mm or 2.5 1')
        value = {'value': match[1], 'unit': match[2]}
    if not isinstance(value, dict) or not isinstance(value.get('unit'), str) or value['unit'] not in UNITS:
        fail('UNSUPPORTED_UNIT')
    return {'value': number(value.get('value')), 'unit': value['unit']}


def converted(value, target_unit):
    value = quantity(value)
    if not isinstance(target_unit, str) or target_unit not in UNITS or UNITS[value['unit']][0] != UNITS[target_unit][0]:
        fail('INCOMPATIBLE_UNITS', f"{value['unit']} -> {target_unit}")
    result = value['value'] * UNITS[value['unit']][1] / UNITS[target_unit][1]
    return number(result)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail('DUPLICATE_JSON_KEY', key)
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda value: fail('NONFINITE_ENGINEERING_NUMBER', value))
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, WorkerError):
            raise
        fail('INVALID_VENDOR_JSON', str(exc))


def _text(params, key):
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        fail('VENDOR_PARAMETER_REQUIRED', key)
    return value


def _metric_requirements(params):
    required = params.get('required_metrics')
    if not isinstance(required, list) or not required or not all(isinstance(x, str) and x for x in required) or len(set(required)) != len(required):
        fail('REQUIRED_METRICS_REQUIRED')
    units = params.get('metric_units')
    if not isinstance(units, dict) or any(not isinstance(units.get(key), str) or units[key] not in UNITS for key in required):
        fail('METRIC_UNITS_REQUIRED')
    rules = params.get('metric_rules', [])
    if not isinstance(rules, list):
        fail('INVALID_METRIC_RULES')
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('name') not in required or not isinstance(rule.get('operator'), str) or rule['operator'] not in COMPARE:
            fail('INVALID_METRIC_RULE')
        converted({'value': rule.get('target'), 'unit': rule.get('unit')}, units[rule['name']])
    return {'required_metrics': required, 'metric_units': units, 'metric_rules': rules}


def requirements(kind, params):
    """Validate before launching a vendor application; no paths or loads are guessed."""
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(params, dict):
        fail('VENDOR_CONTRACT_INVALID')
    report = _text(params, 'report_output')
    if kind == 'catia_parameters':
        source, native, step = (_text(params, key) for key in ('input_model', 'native_output', 'step_output'))
        if not source.lower().endswith('.catpart') or not native.lower().endswith('.catpart') or not step.lower().endswith(('.step', '.stp')):
            fail('UNSUPPORTED_VENDOR_FILE_TYPE')
        updates = params.get('parameter_updates')
        if not isinstance(updates, list) or not updates:
            fail('PARAMETER_UPDATES_REQUIRED')
        seen = set()
        for update in updates:
            if not isinstance(update, dict):
                fail('INVALID_PARAMETER_UPDATE')
            name = _text(update, 'name')
            if name in seen or update.get('allow_relation'):
                fail('INVALID_PARAMETER_UPDATE', name)
            seen.add(name)
            q = quantity(_text(update, 'value'))
            if update.get('unit', q['unit']) != q['unit']:
                fail('INCOMPATIBLE_UNITS', name)
            if number(update.get('tolerance', 1e-6)) < 0:
                fail('INVALID_PARAMETER_TOLERANCE')
        expected = {'parameter_updates': updates}
        outputs = [native, step, report]
    elif kind == 'mechanical_solve':
        source, native, result = (_text(params, key) for key in ('db_file', 'native_output', 'result_output'))
        if not source.lower().endswith(('.mechdb', '.mechdat')) or not native.lower().endswith(('.mechdb', '.mechdat')) or not result.lower().endswith('.json'):
            fail('UNSUPPORTED_VENDOR_FILE_TYPE')
        expected = _metric_requirements(params) | {'analysis_name': _text(params, 'analysis_name')}
        if not set(expected['required_metrics']) <= {'total_deformation', 'equivalent_stress'}:
            fail('UNSUPPORTED_MECHANICAL_RESULT')
        outputs = [native, result, report]
    elif kind in {'adams_export', 'vi_grade_export'}:
        source = _text(params, 'command_file' if kind == 'adams_export' else 'scenario_file')
        result = _text(params, 'result_output' if kind == 'adams_export' else 'metrics_output')
        if not result.lower().endswith(('.csv', '.json')):
            fail('UNSUPPORTED_VENDOR_FILE_TYPE')
        expected = _metric_requirements(params)
        if result.lower().endswith('.csv'):
            columns = params.get('metric_columns')
            if not isinstance(columns, dict) or set(columns) != set(expected['required_metrics']):
                fail('METRIC_COLUMNS_REQUIRED')
            for name, spec in columns.items():
                if not isinstance(spec, dict) or spec.get('reducer') not in {'min', 'max', 'abs_max', 'last'}:
                    fail('INVALID_METRIC_REDUCER', name)
                _text(spec, 'column')
                converted({'value': 0, 'unit': spec.get('unit')}, expected['metric_units'][name])
            expected['metric_columns'] = columns
        outputs = [result, report]
    else:
        fail('UNSUPPORTED_VENDOR_VALIDATION')
    names([source] + outputs)
    if not report.lower().endswith('.json'):
        fail('UNSUPPORTED_VENDOR_FILE_TYPE')
    expected.update({key: value for key, value in params.items() if key in {
        'input_model', 'native_output', 'step_output', 'db_file', 'result_output', 'command_file', 'scenario_file', 'metrics_output', 'report_output'}})
    return source, outputs, expected


def build_contract(kind, params, records, outputs, tool, action):
    if KINDS.get(kind) != (tool, action):
        fail('VENDOR_ACTION_MISMATCH')
    source, required_outputs, expected = requirements(kind, params)
    input_hashes = {r['path']: r['sha256'] for r in records}
    if source not in input_hashes or set(outputs) != set(required_outputs):
        fail('VENDOR_FILE_CONTRACT_MISMATCH')
    return {'schema_version': SCHEMA, 'kind': kind, 'report': expected['report_output'],
            'expectations': expected, 'input_hashes': input_hashes}


def check_contract(contract, tool, action, outputs):
    if not isinstance(contract, dict) or contract.get('schema_version') != SCHEMA or not isinstance(contract.get('kind'), str) or KINDS.get(contract['kind']) != (tool, action):
        fail('VENDOR_CONTRACT_INVALID')
    source, expected_outputs, expected = requirements(contract['kind'], contract.get('expectations', {}))
    hashes = contract.get('input_hashes')
    if (set(expected_outputs) != set(outputs) or contract.get('report') != expected['report_output']
            or not isinstance(hashes, dict) or source not in hashes
            or any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in hashes.values())):
        fail('VENDOR_CONTRACT_INVALID')
    names(list(hashes) + list(outputs))
    return expected


def read_export(data, suffix, expected):
    """Read raw exported numbers again; never choose the last valid CSV cell."""
    required = expected['required_metrics']
    if suffix.lower() == '.json':
        value = strict_json(data)
        if not isinstance(value, dict) or not isinstance(value.get('metrics'), dict) or not isinstance(value.get('units'), dict):
            fail('INVALID_METRICS_EXPORT')
        result = {}
        for name in required:
            if name not in value['metrics'] or name not in value['units']:
                fail('METRIC_MISSING', name)
            result[name] = quantity({'value': value['metrics'][name], 'unit': value['units'][name]})
        return result
    try:
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
        headers = reader.fieldnames
        if not headers or len(set(headers)) != len(headers):
            fail('INVALID_METRIC_COLUMNS')
        rows = list(reader)
    except (csv.Error, UnicodeError) as exc:
        fail('INVALID_METRICS_EXPORT', str(exc))
    if not rows or any(None in row or None in row.values() for row in rows):
        fail('INVALID_METRIC_ROWS')
    result = {}
    for name, spec in expected['metric_columns'].items():
        if spec['column'] not in headers:
            fail('METRIC_MISSING', name)
        values = [number(row[spec['column']]) for row in rows]
        reduce = {'min': min, 'max': max, 'abs_max': lambda vs: max(abs(v) for v in vs), 'last': lambda vs: vs[-1]}
        result[name] = quantity({'value': reduce[spec['reducer']](values), 'unit': spec['unit']})
    return result


def evaluate_quantities(metrics, expected):
    normalized = {}
    for name in expected['required_metrics']:
        if name not in metrics:
            fail('METRIC_MISSING', name)
        unit = expected['metric_units'][name]
        normalized[name] = {'value': converted(metrics[name], unit), 'unit': unit}
    verdicts = []
    for rule in expected['metric_rules']:
        observed = normalized[rule['name']]
        target = converted({'value': rule['target'], 'unit': rule['unit']}, observed['unit'])
        passed = COMPARE[rule['operator']](observed['value'], target)
        verdicts.append({'name': rule['name'], **observed, 'operator': rule['operator'], 'target': target, 'passed': passed})
    return normalized, verdicts


def validate_evidence(report, contract, outputs, *, request_id, tool, action):
    expected = check_contract(contract, tool, action, outputs)
    if not isinstance(report, dict) or report.get('schema_version') != SCHEMA or any(report.get(k) != v for k, v in {
            'request_id': request_id, 'tool': tool, 'action': action}.items()):
        fail('VENDOR_REPORT_MISMATCH')
    if report.get('inputs_after') != contract['input_hashes']:
        fail('VENDOR_INPUT_CHANGED')
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in outputs.items() if name != contract['report']}
    if report.get('artifacts') != hashes:
        fail('VENDOR_ARTIFACT_MISMATCH')
    observation = report.get('observations')
    if not isinstance(observation, dict):
        fail('VENDOR_OBSERVATIONS_MISSING')
    metrics, verdicts = {}, []
    if contract['kind'] == 'catia_parameters':
        if observation.get('reopened_from_disk') is not True:
            fail('NATIVE_REOPEN_REQUIRED')
        values = observation.get('parameters', {})
        for update in expected['parameter_updates']:
            target = quantity(update['value'])
            observed = values.get(update['name'], {})
            for phase in ('after_update', 'after_reopen'):
                value = converted(observed.get(phase), target['unit'])
                if abs(value - target['value']) > number(update.get('tolerance', 1e-6)):
                    fail('PARAMETER_READBACK_MISMATCH', update['name'] + ':' + phase)
        step = outputs[expected['step_output']]
        if not step.lstrip().startswith(b'ISO-10303-21;') or b'END-ISO-10303-21;' not in step:
            fail('STEP_EXPORT_INVALID')
    else:
        result_path = expected.get('result_output', expected.get('metrics_output'))
        metrics = read_export(outputs[result_path], '.' + result_path.rsplit('.', 1)[-1], expected)
        if contract['kind'] == 'mechanical_solve':
            if observation.get('solution_status') != 'Done' or observation.get('analysis_name') != expected['analysis_name']:
                fail('MECHANICAL_SOLUTION_INCOMPLETE')
            if observation.get('evaluated') != {name: True for name in expected['required_metrics']}:
                fail('MECHANICAL_RESULT_UNSOLVED')
        elif type(observation.get('returncode')) is not int or observation['returncode'] != 0:
            fail('VENDOR_PROCESS_FAILED')
        metrics, verdicts = evaluate_quantities(metrics, expected)
    return {'schema_version': SCHEMA, 'passed': True, 'kind': contract['kind'],
            'metrics': metrics, 'metric_verdicts': verdicts,
            'engineering_acceptance': ('failed' if any(not v['passed'] for v in verdicts) else 'passed') if verdicts else 'not_requested',
            'real_machine_verified': False, 'scope': 'file_identity_and_reported_observations'}

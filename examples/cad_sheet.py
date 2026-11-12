"""Generate a dimensioned mounting plate, editable A3 sheet and PDF/PNG exports.

Run after installing SimLoom AI:
    python examples/cad_sheet.py --output /path/to/new-example-folder
The destination must not already exist; this example never replaces a project.
"""
import argparse
from datetime import date
import json
from pathlib import Path

from engineering_agent import EngineeringAgent


def generate(output):
    root = Path(output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    agent = EngineeringAgent({'tools': {}})
    source = root / 'mounting-plate.dxf'
    agent.write_asset(str(source), 'dxf', {
        'polylines': [{'points': [(0, 0), (120, 0), (120, 80), (0, 80)],
                       'closed': True, 'layer': 'OUTLINE'}],
        'circles': [{'x': x, 'y': y, 'radius': 5, 'layer': 'HOLES'} for x, y in [(15, 15), (105, 15), (105, 65), (15, 65)]],
        'texts': [{'x': 30, 'y': 36, 'height': 3, 'text': '4 x DIA 10 THRU', 'layer': 'ANNOTATION'}],
    }, project_dir=root)
    common = {'project_dir': str(root), 'input_file': source.name, 'output_file': source.name, 'auto_repair': False}
    agent.execute_operation('cad.batch_apply', common | {'operations': [
        {'operation': 'cad.dimension_linear_create', 'start': {'x': 0, 'y': 0}, 'end': {'x': 120, 'y': 0},
         'definition': {'x': 60, 'y': -14}},
        {'operation': 'cad.dimension_linear_create', 'start': {'x': 0, 'y': 0}, 'end': {'x': 0, 'y': 80},
         'definition': {'x': -14, 'y': 40}},
        {'operation': 'cad.sheet_prepare', 'name': 'A3', 'paper': 'A3', 'fields': {
            'TITLE': 'Mounting plate', 'NUMBER': 'SL-PLATE-001', 'REV': 'A',
            'DRAWN_BY': 'SimLoom AI', 'DATE': date.today().isoformat()}},
    ]})
    artifacts = {'dxf': str(source)}
    for format_name in ('pdf', 'png'):
        result = agent.execute_operation('cad.plot', common | {
            'output_file': 'exports/mounting-plate.' + format_name, 'layout': 'A3', 'dpi': 150})
        artifacts[format_name] = result['path']
        artifacts[format_name + '_report'] = result['report_path']
    return artifacts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='cad-sheet-example')
    print(json.dumps(generate(parser.parse_args().output), ensure_ascii=False, indent=2))

"""Generate six independently checked native DXF fillets and PDF/PNG previews.

Run: python examples/cad_corners.py --output /path/to/new-example-folder
The destination must not already exist.
"""
import argparse
import json
import math
from pathlib import Path

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument


def generate(output):
    root = Path(output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'corner-examples.dxf'
    agent = EngineeringAgent({'tools': {}})
    lines, texts = [], [
        {'x': 3, 'y': 125, 'height': 4, 'text': 'SimLoom AI - verified line fillets'},
        {'x': 3, 'y': 118, 'height': 2, 'text': 'Native DXF geometry | R2 | Units: mm'},
    ]
    for index, angle in enumerate((30, 60, 90, 120, 150, 165)):
        x, y = 25 + (index % 3) * 60, 78 - (index // 3) * 55
        for rotation in (90 - angle / 2, 90 + angle / 2):
            lines.append({'x1': x, 'y1': y, 'x2': x + 20 * math.cos(math.radians(rotation)),
                          'y2': y + 20 * math.sin(math.radians(rotation)), 'layer': 'PROFILE'})
        texts.append({'x': x - 18, 'y': y - 12, 'height': 2.5, 'text': f'{angle} deg corner / R2'})
    agent.write_asset(str(source), 'dxf', {'lines': lines, 'texts': texts}, project_dir=root)
    ids = [e['id'] for e in CadDocument.open(source).entities if e['type'] == 'LINE']
    common = {'project_dir': str(root), 'input_file': source.name, 'output_file': source.name, 'auto_repair': False}
    result = agent.execute_operation('cad.batch_apply', common | {'operations': [
        {'operation': 'cad.entity_fillet', 'entity_ids': ids[i:i+2], 'radius': 2}
        for i in range(0, len(ids), 2)
    ]})
    artifacts = {'dxf': str(source)}
    report = root / 'corner-checks.json'
    report.write_text(json.dumps({'run_id': result['run_id'], 'checks': result['persisted_validation']['native']['corner_checks'],
                                 'audit_requirements': {'corner_connections': [r['corner_check'] for r in result['results']]}}, indent=2), encoding='utf-8')
    artifacts['checks'] = str(report)
    for format_name in ('pdf', 'png'):
        plot = agent.execute_operation('cad.plot', common | {
            'output_file': 'exports/corner-examples.' + format_name, 'paper': 'A4', 'dpi': 150})
        artifacts[format_name] = plot['path']
    return artifacts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='cad-corners-example')
    print(json.dumps(generate(parser.parse_args().output), ensure_ascii=False, indent=2))

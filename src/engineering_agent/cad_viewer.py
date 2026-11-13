"""Standalone CAD viewer shell; editing delegates to the parent transaction API."""
import json
from html import escape
from pathlib import Path

from .cad_render import native_model_scene

STATIC = Path(__file__).parent / 'static'


def render_cad_viewer(geometry, *, asset_path=None, history=None, source_path=None, scene=None, read_only=False):
    scene = scene if scene is not None else native_model_scene(geometry, source_path)
    controls = []
    for name, props in sorted(scene['layers'].items()):
        checked = ' checked' if props['visible'] else ''
        controls.append(f"<label><input type='checkbox' data-layer='{escape(name, quote=True)}'{checked}> {escape(name)}</label>")
    undo_disabled = '' if not read_only and history and history.get('can_undo') else ' disabled'
    redo_disabled = '' if not read_only and history and history.get('can_redo') else ' disabled'
    history_json = json.dumps(history or {}, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    sheet_names = sorted(geometry.get('sheets') or {})
    sheets = ' · 图纸：' + ' / '.join(escape(name) for name in sheet_names) if sheet_names else ''
    warnings = scene['warnings']
    operation_history = list((geometry.get('metadata') or {}).get('operation_history') or [])
    replay_items = []
    for index, entry in enumerate(operation_history[-100:]):
        operation = str(entry.get('operation', 'CAD operation'))
        steps = entry.get('steps') or []
        if steps:
            for step_index, step in enumerate(steps):
                replay_items.append({
                    'label': f"{operation} · {step.get('operation', 'step')}",
                    'ids': [str(value) for value in step.get('entity_ids', [])],
                    'entry': index, 'step': step_index,
                    'run_id': entry.get('run_id'),
                    'step_index': step.get('index', step_index),
                    'batch': bool((step.get('snapshots') or {}).get('after')),
                })
        else:
            replay_items.append({'label': operation, 'ids': [str(value) for value in entry.get('entity_ids', [])], 'entry': index})
    replay_json = json.dumps(replay_items, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    if replay_items:
        operation_list = ''.join(
            f"<button type='button' class='operation-step' data-history-index='{index}' data-batch='{str(bool(item.get('batch'))).lower()}' data-run-id='{escape(str(item.get('run_id') or ''), quote=True)}' data-step-index='{int(item.get('step_index', item.get('step', 0)))}'>{escape(item['label'])}<span>{len(item['ids'])} 个对象</span></button>"
            for index, item in enumerate(replay_items)
        )
    else:
        operation_list = '<div class="operation-empty">还没有记录变更对象。</div>'
    if warnings:
        notice = '<details class="render-warning"><summary>预览有 ' + str(len(warnings)) + ' 条提示，部分内容可能未显示完整</summary><ul>'
        for item in warnings:
            if item['code'] == 'VIEWER_MISSING_GLYPHS':
                text = '字体缺字：' + item['characters'] + '；请配置包含这些字符的图纸字体。'
            elif item['code'] == 'VIEWER_ENTITY_SKIPPED':
                text = str(item.get('type')) + '（' + str(item.get('handle') or '块内对象') + '）：' + item['reason']
            else:
                text = item.get('message', item['code'])
            notice += '<li>' + escape(text) + '</li>'
        notice += '</ul></details>'
    else:
        notice = ''
    return ''.join([
        '<!doctype html><meta charset="utf-8"><style>', (STATIC / 'cad_viewer.css').read_text(encoding='utf-8'), '</style>',
        f"<div class='toolbar' data-cad-viewer data-read-only='{str(read_only).lower()}' data-asset-path='{escape(asset_path or '', quote=True)}'>",
        f"<button id='undo'{undo_disabled}>撤销</button><button id='redo'{redo_disabled}>重做</button>",
        "<button id='fit'>适合窗口</button><button id='fitSelection'>放大选中</button><button id='clear'>清除选择</button>",
        "<label class='snap-control'><input id='snap' type='checkbox' checked> 对象捕捉</label><select id='snapMode' title='对象捕捉类型'><option value='auto'>智能</option><option value='endpoint'>端点</option><option value='midpoint'>中点</option><option value='center'>中心</option></select><span id='snapStatus' role='status'></span>",
        "<span id='selection' role='status'>未选择实体</span></div>",
        f"<div class='toolbar history-controls'><label>图纸版本 <select id='revision'><option value='current'>{'历史快照' if read_only else '当前文件'}</option></select></label>",
        "<button id='historyPrevious'>上一步</button><button id='historyNext'>下一步</button><button id='replay'>播放历史</button><button id='historyCurrent'>返回当前</button>",
        "<span id='historyStatus' role='status'>历史快照只读；批次支持独立子步骤快照。</span></div>",
        "<details class='layers' open><summary>图层显示</summary><div class='layer-list'>", ''.join(controls), '</div></details>',
        "<div class='toolbar edits'><label>移动 dx <input id='dx' type='number' value='0' step='any'></label><label>dy <input id='dy' type='number' value='0' step='any'></label><button id='move'>移动</button>",
        "<label>旋转角 <input id='angle' type='number' value='0' step='any'></label><label>中心 x <input id='cx' type='number' value='0' step='any'></label><label>y <input id='cy' type='number' value='0' step='any'></label><button id='rotate'>旋转</button><button id='delete' class='danger'>删除</button>",
        "<label>文字 <input id='text' class='text' type='text' placeholder='选中文字实体'></label><button id='updateText'>更新属性</button><label>选择集 <input id='selectionName' class='text' type='text' value='picked'></label><button id='saveSelection'>保存选择集</button><span id='editStatus' role='status'>选择实体后编辑</span></div>",
        "<div id='renderWarnings'>", notice, "</div><details class='operations'><summary>定位变更对象（批次子步骤可独立查看）</summary><div class='operation-list'>", operation_list, '</div></details>',
        f"<script type='application/json' id='operationHistory'>{replay_json}</script>",
        f"<script type='application/json' id='revisionHistory'>{history_json}</script>",
        "<div class='canvas-wrap'>", scene['svg'], '</div>',
        f"<div class='view-footer'><span id='entityCount'>{scene['rendered_entities']} / {scene['entity_count']} 个模型实体{sheets}</span><span id='coordinates'></span><span id='zoom'></span></div>",
        "<div class='view-help'>滚轮缩放 · 中键或空白处拖动平移 · Shift 从左向右框选完整对象、从右向左交叉选择 · 点击切换选择 · 选中单个实体后拖动蓝色夹点编辑几何 · 双击对象放大</div>",
        '<script>', (STATIC / 'cad_viewer.js').read_text(encoding='utf-8'), '</script>',
    ])

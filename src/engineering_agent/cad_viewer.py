"""Standalone CAD viewer shell; editing delegates to the parent transaction API."""
from html import escape
from pathlib import Path

from .cad_render import native_model_scene

STATIC = Path(__file__).parent / 'static'


def render_cad_viewer(geometry, *, asset_path=None, history=None, source_path=None, scene=None):
    scene = scene if scene is not None else native_model_scene(geometry, source_path)
    controls = []
    for name, props in sorted(scene['layers'].items()):
        checked = ' checked' if props['visible'] else ''
        controls.append(f"<label><input type='checkbox' data-layer='{escape(name, quote=True)}'{checked}> {escape(name)}</label>")
    undo_disabled = '' if history and history.get('can_undo') else ' disabled'
    redo_disabled = '' if history and history.get('can_redo') else ' disabled'
    sheet_names = sorted(geometry.get('sheets') or {})
    sheets = ' · 图纸：' + ' / '.join(escape(name) for name in sheet_names) if sheet_names else ''
    warnings = scene['warnings']
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
        f"<div class='toolbar' data-cad-viewer data-asset-path='{escape(asset_path or '', quote=True)}'>",
        f"<button id='undo'{undo_disabled}>撤销</button><button id='redo'{redo_disabled}>重做</button>",
        "<button id='fit'>适合窗口</button><button id='fitSelection'>放大选中</button><button id='replay'>逐个高亮</button><button id='clear'>清除选择</button>",
        "<span id='selection' role='status'>未选择实体</span></div>",
        "<details class='layers' open><summary>图层显示</summary><div class='layer-list'>", ''.join(controls), '</div></details>',
        "<div class='toolbar edits'><label>移动 dx <input id='dx' type='number' value='0' step='any'></label><label>dy <input id='dy' type='number' value='0' step='any'></label><button id='move'>移动</button>",
        "<label>旋转角 <input id='angle' type='number' value='0' step='any'></label><label>中心 x <input id='cx' type='number' value='0' step='any'></label><label>y <input id='cy' type='number' value='0' step='any'></label><button id='rotate'>旋转</button><button id='delete' class='danger'>删除</button>",
        "<label>文字 <input id='text' class='text' type='text' placeholder='选中文字实体'></label><button id='updateText'>更新属性</button><label>选择集 <input id='selectionName' class='text' type='text' value='picked'></label><button id='saveSelection'>保存选择集</button><span id='editStatus' role='status'>选择实体后编辑</span></div>",
        notice, "<div class='canvas-wrap'>", scene['svg'], '</div>',
        f"<div class='view-footer'><span>{scene['rendered_entities']} / {scene['entity_count']} 个模型实体{sheets}</span><span id='coordinates'></span><span id='zoom'></span></div>",
        "<div class='view-help'>滚轮缩放 · 中键或空白处拖动平移 · Shift 拖动框选完整对象 · 点击切换选择 · 双击对象放大</div>",
        '<script>', (STATIC / 'cad_viewer.js').read_text(encoding='utf-8'), '</script>',
    ])

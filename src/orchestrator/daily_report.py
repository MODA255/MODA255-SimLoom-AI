from collections import Counter
from pathlib import Path
import html
import json


def aggregate_daily_report(run):
    run = run or {}; branches = run.get('branches') or {}
    statuses = Counter(str(b.get('status') or 'unknown') for b in branches.values())
    failures=[]; causes=Counter(); phases=[]
    for branch_id, branch in branches.items():
        analysis=branch.get('failure_analysis') or {}
        if branch.get('status') in {'failed','blocked','waiting'} and analysis:
            item={'branch_id':branch_id, **analysis}; failures.append(item)
            if analysis.get('root_cause'): causes[analysis['root_cause']] += 1
        for metric in (branch.get('task_graph') or {}).get('phase_metrics', []):
            if metric.get('duration_seconds') is not None: phases.append({**metric,'branch_id':branch_id})
    overall = run.get('result') if run.get('result') in {'passed','partial_success','failed','blocked','waiting'} else ('passed' if statuses and all(k == 'passed' for k in statuses) else 'partial_success' if statuses.get('passed') else 'blocked')
    return {'run_id':run.get('run_id'),'overall':overall,'status_counts':dict(statuses),'failure_count':len(failures),'root_cause_counts':dict(causes),'slowest_phase':max(phases,key=lambda x:x.get('duration_seconds',0)) if phases else None,'failures':failures,'phase_metrics':phases}


def render_daily_report(report):
    daily = report.get('daily_report', report)
    lines=[f"# 每日测试报告 {report.get('run_id', daily.get('run_id',''))}", '', f"- 总体结果：`{daily.get('overall')}`", f"- 失败数量：{daily.get('failure_count',0)}", '', '## 状态统计']
    for k,v in (daily.get('status_counts') or {}).items(): lines.append(f'- {k}: {v}')
    lines += ['', '## 根因统计']
    for k,v in (daily.get('root_cause_counts') or {}).items(): lines.append(f'- {k}: {v}')
    slow=daily.get('slowest_phase')
    if slow: lines += ['', f"## 最慢阶段", f"- {slow.get('name')}: {slow.get('duration_seconds')} 秒（{slow.get('branch_id','')}）"]
    lines += ['', '## 失败明细']
    for item in daily.get('failures',[]): lines.append(f"- {item.get('branch_id')}: {item.get('root_cause')} → {item.get('next_action')}")
    return '\n'.join(lines) + '\n'


def _render_html(report):
    daily = report.get('daily_report', report)
    status_rows = ''.join(f"<tr><td>{html.escape(str(k))}</td><td>{v}</td></tr>" for k,v in (daily.get('status_counts') or {}).items()) or '<tr><td colspan="2">无状态数据</td></tr>'
    root_rows = ''.join(f"<tr><td>{html.escape(str(k))}</td><td>{v}</td></tr>" for k,v in (daily.get('root_cause_counts') or {}).items()) or '<tr><td colspan="2">无根因数据</td></tr>'
    failure_rows = ''.join(f"<tr><td>{html.escape(str(item.get('branch_id','')))}</td><td>{html.escape(str(item.get('root_cause','')))}</td><td>{html.escape(str(item.get('next_action','')))}</td></tr>" for item in daily.get('failures', [])) or '<tr><td colspan="3">无失败明细</td></tr>'
    phase_rows = ''.join(f"<tr><td>{html.escape(str(item.get('branch_id','')))}</td><td>{html.escape(str(item.get('name','')))}</td><td>{item.get('duration_seconds','-')} 秒</td></tr>" for item in daily.get('phase_metrics', [])) or '<tr><td colspan="3">无阶段数据</td></tr>'
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>每日测试报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;background:#f5f7fa;color:#172033;margin:32px}}section{{background:#fff;padding:20px;margin:16px 0;border-radius:8px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #dfe3e8;padding:8px;text-align:left}}th{{background:#eef1f5}}.status{{font-size:22px;font-weight:bold}}</style></head><body><h1>每日测试详细报告</h1><p>运行编号：{html.escape(str(report.get('run_id',daily.get('run_id',''))))}</p><section><h2>运行总览</h2><p class='status'>{html.escape(str(daily.get('overall','unknown')))}</p><p>失败数量：{daily.get('failure_count',0)}；阶段数量：{len(daily.get('phase_metrics') or [])}</p></section><section><h2>状态统计</h2><table><tr><th>状态</th><th>数量</th></tr>{status_rows}</table></section><section><h2>根因统计</h2><table><tr><th>根因</th><th>数量</th></tr>{root_rows}</table></section><section><h2>失败明细</h2><table><tr><th>分支</th><th>根因</th><th>下一动作</th></tr>{failure_rows}</table></section><section><h2>运行时间线</h2><table><tr><th>分支</th><th>阶段</th><th>耗时</th></tr>{phase_rows}</table></section></body></html>"""


def write_daily_report(report, directory):
    root=Path(directory); root.mkdir(parents=True,exist_ok=True); run_id=report.get('run_id','daily')
    json_path=root/f'{run_id}.json'; markdown_path=root/f'{run_id}.md'; html_path=root/f'{run_id}.html'
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); markdown_path.write_text(render_daily_report(report),encoding='utf-8'); html_path.write_text(_render_html(report),encoding='utf-8')
    return {'json':str(json_path),'markdown':str(markdown_path),'html':str(html_path)}

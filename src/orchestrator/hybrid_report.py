from datetime import datetime
import html
import json
from statistics import median


def _duration(start, finish):
    if not start or not finish:
        return None
    return round((datetime.fromisoformat(finish.replace('Z', '+00:00')) - datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds(), 3)


def _poll_duration(events):
    stamps = [e.get('observed_at') for e in (events or []) if e.get('observed_at')]
    return _duration(stamps[0], stamps[-1]) if len(stamps) >= 2 else None


def _safe(value):
    return html.escape(str(value), quote=True)


def _json_block(value):
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _chain_view(chain):
    phases = chain.get('phases') or []
    if isinstance(phases, dict):
        phases = [{'phase_name': name, **(value if isinstance(value, dict) else {'duration_ms': value})} for name, value in phases.items()]
    durations = {p.get('phase_name') or p.get('name'): p.get('duration_ms') for p in phases if isinstance(p, dict)}
    diagnostics = []
    retry_count = 0
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        retry_count += int(phase.get('retry_count') or 0)
        if phase.get('error'):
            diagnostics.append({'phase': phase.get('phase_name') or phase.get('name'), 'message': phase.get('error'), 'status': phase.get('status'), 'type': 'phase_error'})
    for error in chain.get('errors') or []:
        if isinstance(error, str):
            error = {'type': 'legacy', 'message': error}
        if not any(d.get('message') == error.get('message') for d in diagnostics):
            diagnostics.append({'phase': error.get('phase'), 'message': error.get('message'), 'status': 'error', 'type': error.get('type')})
    retry_count += sum(int(e.get('retry_count') or 0) for e in chain.get('errors') or [] if isinstance(e, dict))
    elapsed = _duration(chain.get('started_at'), chain.get('finished_at'))
    timing_source = 'task_timestamps' if elapsed is not None else 'poll_events' if _poll_duration(chain.get('poll_events')) is not None else 'missing'
    elapsed = elapsed if elapsed is not None else _poll_duration(chain.get('poll_events'))
    raw_status = chain.get('status')
    normalized_status = 'warning' if raw_status == 'passed' and diagnostics else raw_status
    evidence = {
        'nodes': chain.get('nodes') or [],
        'diagnostics': chain.get('diagnostics') or [],
        'files': chain.get('evidence_files') or [],
        'screenshots': chain.get('screenshots') or [],
        'screenshot_status': chain.get('screenshot_status') or ('collected' if chain.get('screenshots') else 'not_collected'),
    }
    evidence_total = 6
    evidence_done = sum(bool(evidence.get(key)) for key in ('nodes', 'diagnostics', 'files', 'screenshots')) + int(evidence['screenshot_status'] == 'collected') + int(bool(chain.get('videos')))
    return {**chain, 'status': normalized_status, 'duration_seconds': elapsed, 'timing_source': timing_source, 'phase_durations': durations, 'diagnostics': diagnostics, 'diagnostics_summary': diagnostics, 'retry_count': retry_count, 'evidence': evidence, 'evidence_score': {'completed': evidence_done, 'total': evidence_total, 'percentage': round(evidence_done / evidence_total * 100)}, 'videos': chain.get('videos') or [], 'phases_normalized': phases}


def build_run_report(run):
    chains = [_chain_view(chain) for chain in run.get('chains') or []]
    durations = [c['duration_seconds'] for c in chains if c.get('duration_seconds') is not None]
    summary = {'chain_count': len(chains), 'passed': sum(c.get('status') == 'passed' for c in chains), 'failed': sum(c.get('status') == 'failed' for c in chains), 'warnings': sum(c.get('status') not in {'passed', 'failed'} for c in chains), 'error_count': sum(len(c.get('errors') or []) for c in chains), 'retry_count': sum(c.get('retry_count') or 0 for c in chains), 'total_duration_seconds': round(sum(durations), 3) if durations else 0, 'median_duration_seconds': median(durations) if durations else None, 'poll_event_count': sum(len(c.get('poll_events') or []) for c in chains), 'node_count': sum(len(c['evidence']['nodes']) if isinstance(c['evidence']['nodes'], list) else 0 for c in chains), 'diagnostic_count': sum(len(c['evidence']['diagnostics']) for c in chains), 'screenshot_count': sum(len(c['evidence']['screenshots']) for c in chains), 'video_count': sum(len(c.get('videos') or []) for c in chains)}
    rows = ''.join(f"<tr><td>{_safe(c.get('chain_id'))}</td><td>{_safe(c.get('status'))}</td><td>{c.get('duration_seconds','-')}s</td><td>{c['evidence_score']['percentage']}%</td><td>{len(c.get('poll_events') or [])}</td><td>{len(c['evidence']['nodes']) if isinstance(c['evidence']['nodes'],list) else 0}</td><td>{len(c['evidence']['screenshots'])}</td><td>{len(c.get('videos') or [])}</td></tr>" for c in chains)
    phase_rows = ''.join(f"<tr><td>{_safe(c.get('chain_id'))}</td><td>{_safe(name)}</td><td>{_safe(value.get('status') if isinstance(value,dict) else '')}</td><td>{_safe(value.get('duration_ms','-') if isinstance(value,dict) else value)} ms</td></tr>" for c in chains for name,value in c.get('phase_durations',{}).items()) or '<tr><td colspan="4">暂无阶段数据</td></tr>'
    error_rows = ''.join(f"<tr><td>{_safe(c.get('chain_id'))}</td><td>{_safe(d.get('phase'))}</td><td>{_safe(d.get('type'))}</td><td>{_safe(d.get('message'))}</td></tr>" for c in chains for d in c.get('diagnostics_summary',[])) or '<tr><td colspan="4">无错误</td></tr>'
    chain_sections = []
    for c in chains:
        ev=c['evidence']; task_payload=c.get('submission') or c.get('payload') or {}
        poll_rows=''.join(f"<tr><td>{i+1}</td><td>{_safe(e.get('observed_at'))}</td><td>{_safe(e.get('status'))}</td><td>{_safe(e.get('current_phase'))}</td><td>{_safe(e.get('progress_percentage'))}</td><td>{_safe(e.get('completed_phases'))}/{_safe(e.get('total_phases'))}</td><td>{_safe(e.get('error_msg'))}</td></tr>" for i,e in enumerate(c.get('poll_events') or [])) or '<tr><td colspan="7">无轮询事件</td></tr>'
        phase_detail=''.join(f"<details><summary>{_safe(p.get('phase_name') or p.get('name'))}｜{_safe(p.get('status'))}｜{_safe(p.get('duration_ms','-'))} ms</summary><pre>{_json_block(p)}</pre></details>" for p in c.get('phases_normalized',[])) or '无阶段详情'
        nodes=''.join(f"<details><summary>{_safe(n.get('key'))}｜{_safe(n.get('status'))}</summary><pre>{_json_block(n)}</pre></details>" for n in ev['nodes'] if isinstance(n, dict)) or '无节点证据（可能接口不支持或未返回）'
        diags=''.join(f"<details><summary>{_safe(d.get('name'))}｜{_safe(d.get('status'))}</summary><pre>{_json_block(d)}</pre></details>" for d in ev['diagnostics']) or '无诊断文件'
        shots=''.join(f"<a href='{_safe(s)}'><img class='thumb' src='{_safe(s)}' alt='截图'></a>" for s in ev['screenshots']) or f"截图未采集：{_safe(ev['screenshot_status'])}"
        videos=''.join(f"<p><a href='{_safe(v.get('oss_url') or v.get('remote_url') or v.get('url'))}'>打开远程成片 {_safe(v.get('id') or v.get('video_id'))}</a>｜时长 {_safe(v.get('duration'))}｜分辨率 {_safe(v.get('resolution'))}</p>" for v in c.get('videos') or []) or '无成片'
        attribution = c.get('failure_attribution') or {}
        preflight = c.get('preflight') or {}
        chain_sections.append(f"<section class='chain'><h2>{_safe(c.get('chain_id'))}｜{_safe(c.get('status'))}</h2><p>执行模式：{_safe(c.get('execution_mode'))}｜场景：{_safe(c.get('scene_type'))}｜任务 ID：{_safe(c.get('task_id') or (c.get('task') or {}).get('id'))}｜耗时：{_safe(c.get('duration_seconds'))} 秒｜证据完整率：{c['evidence_score']['percentage']}%</p><h3>失败归因</h3><pre>{_json_block(attribution or {'category': 'not_failed'})}</pre><h3>创建前检查</h3><pre>{_json_block(preflight or {'status': 'not_run'})}</pre><h3>提交参数</h3><details open><summary>展开原始提交参数</summary><pre>{_json_block(task_payload)}</pre></details><h3>轮询时间线</h3><table><tr><th>#</th><th>时间</th><th>状态</th><th>阶段</th><th>进度</th><th>阶段计数</th><th>错误</th></tr>{poll_rows}</table><h3>阶段明细</h3>{phase_detail}<h3>节点审计</h3>{nodes}<h3>诊断文件</h3>{diags}<h3>截图墙</h3><div>{shots}</div><h3>成片信息</h3>{videos}<h3>证据完整性</h3><p>节点：{bool(ev['nodes'])}｜诊断：{bool(ev['diagnostics'])}｜截图：{ev['screenshot_status']}｜成片：{bool(c.get('videos'))}｜评分：{c['evidence_score']['completed']}/{c['evidence_score']['total']}</p><h3>原始证据索引</h3><h4>证据文件</h4><pre>{_json_block({'poll_events': c.get('poll_events',[]), 'evidence_files': ev['files'], 'screenshots': ev['screenshots'], 'videos': c.get('videos',[])})}</pre></section>")
    page=f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>MatrixGen 超详细审计报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;background:#f3f5f8;color:#172033;margin:24px}}section{{background:#fff;padding:20px;margin:16px 0;border-radius:10px;box-shadow:0 1px 4px #d8dce2}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border:1px solid #dfe3e8;padding:7px;text-align:left;vertical-align:top}}th{{background:#eef1f5}}pre{{white-space:pre-wrap;overflow:auto;background:#f6f8fa;padding:12px;border-radius:6px;max-height:480px}}details{{margin:8px 0;border:1px solid #e1e5ea;padding:8px;border-radius:6px}}summary{{cursor:pointer;font-weight:600}}.thumb{{max-width:280px;max-height:180px;margin:8px;border:1px solid #ccd3dc}}.chain h2{{border-bottom:2px solid #4d7cff;padding-bottom:8px}}.metric{{display:inline-block;margin:6px;padding:12px;background:#eef4ff;border-radius:8px}}</style></head><body><h1>MatrixGen 每日巡检｜超详细审计报告</h1><p>运行编号：{_safe(run.get('run_id'))}｜编排器版本：{_safe(run.get('orchestrator_commit','未知'))}</p><section><h2>运行总览</h2>{''.join(f"<span class='metric'>{_safe(k)}：{_safe(v)}</span>" for k,v in summary.items())}</section><section><h2>链路矩阵</h2><table><tr><th>链路</th><th>状态</th><th>耗时</th><th>证据完整率</th><th>轮询</th><th>节点</th><th>截图</th><th>成片</th></tr>{rows}</table></section><section><h2>阶段耗时总表</h2><table><tr><th>链路</th><th>阶段</th><th>状态</th><th>耗时</th></tr>{phase_rows}</table></section><section><h2>错误与诊断汇总</h2><table><tr><th>链路</th><th>阶段</th><th>类型</th><th>错误</th></tr>{error_rows}</table></section>{''.join(chain_sections)}</body></html>"""
    return {'summary': summary, 'chains': chains, 'metadata': {k: run.get(k) for k in ('run_id','orchestrator_commit','matrix_version','started_at','finished_at') if run.get(k) is not None}, 'html': page}

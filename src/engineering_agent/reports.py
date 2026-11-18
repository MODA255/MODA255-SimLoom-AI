"""Reports built only from persisted run evidence, never from today's project files."""
from datetime import datetime
from html import escape
import json


STATUS_LABELS = {"queued": "待运行", "running": "运行中", "waiting": "等待恢复连接", "completed": "已完成", "failed": "失败",
                 "planned": "排练通过", "blocked": "依赖阻塞", "unavailable": "不可用", "rolled_back": "已回滚"}

REPAIR_REPORT_SCHEMA = "simloom-ai/task-repair-report/v1"


def _compact_engineering_result(value):
    """Keep the report-facing, bounded part of one EngineeringResult envelope."""
    if not isinstance(value, dict):
        return None
    keys = ("schema_version", "tool", "action", "status", "engineering_acceptance",
            "real_machine_verified", "metrics", "metric_verdicts", "metric_evidence",
            "observations", "contract", "lifecycle", "failure", "failure_evidence")
    return {key: value[key] for key in keys if key in value}


def _engineering_result_records(result):
    """Collect persisted vendor envelopes without inventing evidence.

    External runs store envelopes under ``results[*].details`` while a few
    workflow adapters persist one top-level envelope.  Reports expose both in
    one bounded list so Web, HTML and future clients use the same source.
    """
    records = []
    seen = set()

    def add(value, *, step_id=None, tool=None, action=None):
        compact = _compact_engineering_result(value)
        if compact is None:
            return
        identity = (step_id or compact.get("tool"), tool or compact.get("tool"),
                    action or compact.get("action"), compact.get("status"))
        if identity in seen:
            return
        seen.add(identity)
        records.append({"step_id": step_id, "tool": tool or compact.get("tool"),
                        "action": action or compact.get("action"),
                        "engineering_result": compact})

    add(result.get("engineering_result"))
    for item in result.get("results", []) if isinstance(result.get("results"), list) else []:
        if not isinstance(item, dict):
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        add(details.get("engineering_result"), step_id=item.get("step_id"),
            tool=item.get("tool"), action=item.get("action"))
    for item in result.get("steps", []) if isinstance(result.get("steps"), list) else []:
        if not isinstance(item, dict):
            continue
        add(item.get("engineering_result"), step_id=item.get("id") or item.get("step_id"),
            tool=item.get("tool"), action=item.get("action"))
    return records[:64]


def _repair_report(result):
    """Normalize bounded repair evidence for task reports.

    A repair can be attached to a run in two compatible shapes: the regular
    ``validation.auto_repair`` envelope produced by :class:`EngineeringAgent`
    and the direct ``repair``/``repair_loop`` fields returned by workflow
    endpoints.  The task report deliberately keeps the evidence descriptive:
    it does not recalculate metric trends or infer a successful repair from a
    child process status.  The comparison and decision are copied from the
    persisted workflow result, with the same small limits used by the repair
    engine so a malformed worker response cannot make the Web page unbounded.
    """
    if not isinstance(result, dict):
        return None
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    auto = validation.get("auto_repair") if isinstance(validation.get("auto_repair"), dict) else {}
    direct = result.get("repair") if isinstance(result.get("repair"), dict) else {}
    loop = (auto.get("repair_loop") if isinstance(auto.get("repair_loop"), dict) else None) or \
        (direct.get("repair_loop") if isinstance(direct.get("repair_loop"), dict) else None) or \
        (result.get("repair_loop") if isinstance(result.get("repair_loop"), dict) else None)
    comparison = (direct.get("comparison") if isinstance(direct.get("comparison"), dict) else None) or \
        (auto.get("comparison") if isinstance(auto.get("comparison"), dict) else None)
    decision = (direct.get("decision") if isinstance(direct.get("decision"), dict) else None) or \
        (auto.get("decision") if isinstance(auto.get("decision"), dict) else None) or \
        ((loop or {}).get("decision") if isinstance((loop or {}).get("decision"), dict) else None)
    candidate = (direct.get("candidate") if isinstance(direct.get("candidate"), dict) else None) or \
        (auto.get("candidate") if isinstance(auto.get("candidate"), dict) else None)
    parent_id = direct.get("parent_workflow_id") or auto.get("parent_workflow_id")
    child_id = direct.get("child_workflow_id") or auto.get("child_workflow_id")
    requested = bool(auto.get("requested") or direct or loop or comparison or decision)
    if not requested:
        return None

    attempts = []
    if isinstance(loop, dict) and isinstance(loop.get("attempts"), list):
        for item in loop["attempts"][:8]:
            if not isinstance(item, dict):
                continue
            attempt = {key: item[key] for key in ("index", "status", "workflow_id") if key in item}
            if isinstance(item.get("candidate"), dict):
                attempt["candidate"] = {key: item["candidate"][key] for key in (
                    "schema_version", "strategy_id", "step_id", "reason", "parameter_patch", "evidence", "max_attempts"
                ) if key in item["candidate"]}
            if isinstance(item.get("comparison"), dict):
                attempt["comparison"] = {key: item["comparison"][key] for key in (
                    "schema_version", "parent_workflow_id", "child_workflow_id", "parent", "child",
                    "metrics", "counts", "outcome", "next_action", "should_continue"
                ) if key in item["comparison"]}
                if isinstance(attempt["comparison"].get("metrics"), list):
                    attempt["comparison"]["metrics"] = attempt["comparison"]["metrics"][:256]
            if isinstance(item.get("decision"), dict):
                attempt["decision"] = {key: item["decision"][key] for key in (
                    "status", "reason", "next_action"
                ) if key in item["decision"]}
            attempts.append(attempt)
    comparison_copy = None
    if isinstance(comparison, dict):
        comparison_copy = {key: comparison[key] for key in (
            "schema_version", "parent_workflow_id", "child_workflow_id", "candidate",
            "parent", "child", "metrics", "counts", "outcome", "next_action", "should_continue"
        ) if key in comparison}
        if isinstance(comparison_copy.get("metrics"), list):
            comparison_copy["metrics"] = comparison_copy["metrics"][:256]
    loop_copy = None
    if isinstance(loop, dict):
        loop_copy = {key: loop[key] for key in ("schema_version", "budget", "attempt_count", "attempts", "decision") if key in loop}
        loop_copy["attempts"] = attempts
    return {
        "schema_version": REPAIR_REPORT_SCHEMA,
        "requested": bool(auto.get("requested", requested)),
        "status": auto.get("status") or direct.get("status") or result.get("status"),
        "parent_workflow_id": parent_id,
        "child_workflow_id": child_id,
        "candidate": candidate,
        "comparison": comparison_copy,
        "decision": decision,
        "loop": loop_copy,
        "attempts": attempts,
    }


def build_report(queue, task_id):
    task = queue.get(task_id)
    if not task:
        raise FileNotFoundError("任务不存在")
    events = queue.events(task_id)
    result = task.get("result") or {}
    stages, plan = {}, result.get("plan")
    started = next((e["created_at"] for e in events if e["event"] == "running"), None)
    finished = task["updated_at"] if task["status"] in {"completed", "failed"} else None
    for event in events:
        payload = event["payload"]
        if event["event"] in {"stage_started", "stage_finished"}:
            stages[payload["id"]] = payload
        elif event["event"] == "plan":
            plan = payload
    if not stages:
        for i, item in enumerate(result.get("stages", result.get("results", []))):
            stages[str(i)] = {**item, "id": str(i), "label": item.get("tool", "阶段"), "duration_seconds": None}
    error = result.get("error") or next((s.get("error") or s.get("message") for s in stages.values()
                                         if s.get("status") in {"failed", "blocked", "unavailable"}), None)
    if task["status"] == "failed" and not error:
        error = "任务未完成，请检查执行计划和事件记录。"
    if task["status"] == "waiting":
        error = None
    verdicts = result.get("verdicts", [])
    verdict_status = ("failed" if any(not v["passed"] for v in verdicts) else "passed") if verdicts else "not_evaluated"
    mode = next((e["payload"]["mode"] for e in events if e["event"] == "execution_mode"), task["mode"])
    offline = mode == "offline"
    external = mode == "external"
    snapshot = task["input"]
    engineering_results = _engineering_result_records(result)
    repair = _repair_report(result)
    return {
        "schema_version": "simloom-ai/task-report/v1",
        "task_id": task_id, "project_id": task["project_id"], "project_dir": task["project_dir"],
        "project_name": (snapshot or {}).get("project_name", "历史项目"),
        "prompt": task["prompt"], "status": task["status"], "retry_of": task.get("retry_of"),
        "mode": mode, "mode_label": "离线示例" if offline else ("外部软件调用" if external else "计划排练"),
        "backend": result.get("simulation_backend", "offline-surrogate" if offline else ("external-adapter" if external else "dry-run")),
        "notice": ("已尝试调用外部软件；运行完成状态不代表求解或工程指标通过。" if external else "离线示例数据用于检查流程，不代表商业软件的真实求解结果。" if offline else
                   "排练只生成输入并检查流程，未启动商业软件求解。"),
        "created_at": task["created_at"], "started_at": started, "finished_at": finished,
        "duration_seconds": round((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds(), 6)
                            if started and finished else None,
        "input": snapshot, "input_snapshot_available": snapshot is not None,
        "plan": plan, "stages": list(stages.values()), "verdicts": verdicts,
        "engineering_results": engineering_results,
        "repair": repair,
        "validation_status": verdict_status,
        "artifacts": result.get("artifact_manifest", []), "error": error,
        "workflow_id": task.get("workflow_id"),
        "resume_allowed": bool(task.get("workflow_id")) and task["status"] in {"waiting", "failed", "running"},
        "retry_allowed": task["status"] == "failed" and snapshot is not None and not external,
        "next_step": ("恢复原工程链：先检查原任务，再继续剩余步骤；不会自动重跑状态不明的操作。" if task.get("workflow_id") and task["status"] != "completed" else
                      "检查失败阶段及外部软件日志；通过 CLI 重新提交任务并明确执行模式。" if external and error else
                      "检查失败阶段及其输入；修复环境后，可使用原参数重试。" if error else
                      "可以查看产物并导出报告。" if task["status"] == "completed" else "等待任务完成。"),
        "evidence_note": "报告使用该任务保存的输入快照与运行时记录的文件哈希；旧任务缺失的信息不补造。",
        "events": events,
    }


def render_report(report):
    def e(value):
        return escape(str(value if value is not None else "未记录"), quote=True)
    def pretty(value):
        return e(json.dumps(value, ensure_ascii=False, indent=2))
    def table(headers, rows):
        return '<div class="table-wrap"><table><thead><tr>' + ''.join('<th>' + e(h) + '</th>' for h in headers) + \
               '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + e(c) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    stages = table(["阶段", "软件", "状态", "耗时（秒）", "说明"], [
        [s.get("label"), s.get("tool") or "工作台", STATUS_LABELS.get(s.get("status"), s.get("status")),
         s.get("duration_seconds"), s.get("error") or s.get("message") or ""] for s in report["stages"]])
    metrics = table(["指标", "数值", "单位", "规则", "判定"], [
        [v["name"], v.get("value"), v.get("unit"), str(v["operator"]) + " " + str(v["target"]),
         "通过" if v["passed"] else "未通过"] for v in report["verdicts"]])
    evidence_rows = []
    for record in report.get("engineering_results", []):
        envelope = record.get("engineering_result") or {}
        for evidence in envelope.get("metric_evidence", []) if isinstance(envelope.get("metric_evidence"), list) else []:
            source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
            evidence_rows.append([
                record.get("step_id") or record.get("action") or "未记录",
                record.get("tool") or envelope.get("tool") or "未记录",
                evidence.get("name"), source.get("path"),
                source.get("value_selector") or source.get("column"),
                source.get("unit_selector") or source.get("reducer") or "",
            ])
    evidence = table(["步骤", "软件", "指标", "来源文件", "值定位", "单位/聚合"], evidence_rows) \
        if evidence_rows else '<p>本次任务没有保存指标来源记录。</p>'
    failure_rows = []
    failure_payloads = []
    for record in report.get("engineering_results", []):
        envelope = record.get("engineering_result") or {}
        failure_evidence = envelope.get("failure_evidence")
        if not isinstance(failure_evidence, dict):
            continue
        failure_rows.append([
            record.get("step_id") or record.get("action") or "未记录",
            record.get("tool") or envelope.get("tool") or "未记录",
            failure_evidence.get("phase"), failure_evidence.get("error_code"),
            failure_evidence.get("category"), failure_evidence.get("recommended_action"),
            ", ".join(failure_evidence.get("sources", [])) if isinstance(failure_evidence.get("sources"), list) else "",
        ])
        failure_payloads.append({
            "step_id": record.get("step_id"), "tool": record.get("tool") or envelope.get("tool"),
            "action": record.get("action") or envelope.get("action"),
            "failure_evidence": failure_evidence,
        })
    failure_evidence_view = table(["步骤", "软件", "阶段", "错误码", "分类", "建议动作", "来源"], failure_rows) \
        if failure_rows else '<p>本次任务没有保存结构化失败证据。</p>'
    if failure_payloads:
        failure_evidence_view += '<details><summary>查看失败证据 JSON</summary><pre>' + pretty(failure_payloads) + '</pre></details>'
    repair = report.get("repair") or {}
    repair_sections = ''
    if repair:
        candidate = repair.get("candidate") or {}
        comparison = repair.get("comparison") or {}
        decision = repair.get("decision") or {}
        loop = repair.get("loop") or {}
        repair_metrics = table(["步骤", "指标", "父值", "子值", "变化", "趋势", "父/子判定"], [
            [item.get("step_id"), item.get("name"),
             (item.get("parent") or {}).get("value"), (item.get("child") or {}).get("value"),
             item.get("delta"), item.get("trend"),
             f"{(item.get('parent') or {}).get('passed')} / {(item.get('child') or {}).get('passed')}" ]
            for item in comparison.get("metrics", []) if isinstance(item, dict)
        ]) if comparison.get("metrics") else '<p>没有可比较的父子指标证据。</p>'
        repair_attempts = table(["尝试", "子工程链", "策略", "结果", "停止原因"], [
            [item.get("index"), item.get("workflow_id"), (item.get("candidate") or {}).get("strategy_id"),
             ((item.get("comparison") or {}).get("outcome") or item.get("status")),
             (item.get("decision") or {}).get("reason") or ""]
            for item in repair.get("attempts", []) if isinstance(item, dict)
        ]) if repair.get("attempts") else '<p>本次没有保存多轮修复尝试。</p>'
        repair_sections = f'''<section><h2>修复比较与重规划</h2>
<p>父工程链：{e(repair.get("parent_workflow_id"))}<br>子工程链：{e(repair.get("child_workflow_id"))}<br>
策略：{e(candidate.get("strategy_id"))} · 当前状态：{e(repair.get("status"))}<br>
比较结果：{e(comparison.get("outcome"))} · 下一步：{e(decision.get("next_action"))} · 停止原因：{e(decision.get("reason"))}<br>
修复预算：{e(loop.get("budget"))} · 已尝试：{e(loop.get("attempt_count"))}</p>
<h3>父子指标变化</h3>{repair_metrics}<h3>修复时间线</h3>{repair_attempts}
<details><summary>查看修复证据 JSON</summary><pre>{pretty(repair)}</pre></details></section>'''
    files = table(["产物", "相对路径", "字节", "SHA-256"], [
        [a["name"], a["path"], a["bytes"], a["sha256"]] for a in report["artifacts"]])
    timeline = table(["时间（UTC）", "事件", "说明"], [
        [v["created_at"], v["event"], v["payload"].get("label") or v["payload"].get("error") or ""] for v in report["events"]])
    validation = {"passed": "全部规则通过", "failed": "存在未通过的规则", "not_evaluated": "未进行指标判定"}[report["validation_status"]]
    failure = '<section class="failure"><h2>失败原因</h2><p>' + e(report["error"]) + '</p><p>' + e(report["next_step"]) + '</p></section>' if report["error"] else ''
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{e(report['project_name'])} · SimLoom AI 工程报告</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf1f7;color:#172033;font:14px/1.65 system-ui,sans-serif}}
main{{max-width:1150px;margin:36px auto;padding:0 24px}}header{{padding:32px;border-radius:20px;background:#172849;color:#fff}}
h1{{font-size:30px;margin:8px 0}}h2{{font-size:18px}}.brand{{letter-spacing:.15em;color:#b8ccff}}.notice{{background:#fff5df;padding:16px;border-radius:10px;color:#704b0d}}
section{{background:white;border:1px solid #dfe5ef;border-radius:14px;padding:22px;margin:18px 0}}.facts{{display:flex;gap:24px;flex-wrap:wrap}}
.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #e4e8ef;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere;max-width:400px}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:16px;border-radius:10px}}.failure{{border-color:#e7b1bb;color:#9d2c40}}small{{color:#63728a}}
@media print{{body{{background:white}}main{{margin:0;padding:0}}section{{break-inside:avoid}}header{{background:white;color:#172033}}.table-wrap{{overflow:visible}}}}
</style></head><body><main><header><div class="brand">SIMLOOM AI / ENGINEERING REPORT</div>
<h1>{e(report['project_name'])}</h1><p>{e(report['prompt'])}</p>
<div class="facts"><span>{e(report['mode_label'])}</span><span>任务：{e(STATUS_LABELS.get(report['status'], report['status']))}</span><span>指标：{e(validation)}</span></div></header>
<p class="notice">{e(report['notice'])}</p>{failure}
<section><h2>运行记录</h2><p>任务编号：{e(report['task_id'])}<br>重试来源：{e(report['retry_of'])}<br>开始：{e(report['started_at'])}<br>结束：{e(report['finished_at'])}<br>执行耗时：{e(report['duration_seconds'])} 秒</p><small>{e(report['evidence_note'])}</small></section>
<section><h2>输入与规则快照</h2><pre>{pretty(report['input'])}</pre></section>
<section><h2>执行计划</h2><pre>{pretty(report['plan'])}</pre></section>
<section><h2>阶段与耗时</h2>{stages}</section>
<section><h2>工程指标</h2><p>{e(validation)}</p>{metrics}</section>
<section><h2>指标来源证据</h2>{evidence}</section>
<section><h2>软件失败证据</h2><p>以下内容来自保存的 worker/runner 诊断；它不会替代厂商原生报告，也不会单独证明工程验收。</p>{failure_evidence_view}</section>
{repair_sections}
<section><h2>产物与校验值</h2><p>以下路径相对于项目目录。历史副本在后续运行中保留。</p>{files}</section>
<section><h2>事件时间线</h2>{timeline}</section>
<footer><small>SimLoom AI · {e(report['schema_version'])} · 可离线阅读，也可通过浏览器打印为 PDF。</small></footer>
</main></body></html>'''

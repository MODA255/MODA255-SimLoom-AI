"""Reports built only from persisted run evidence, never from today's project files."""
from datetime import datetime
from html import escape
import json


STATUS_LABELS = {"queued": "待运行", "running": "运行中", "waiting": "等待恢复连接", "completed": "已完成", "failed": "失败",
                 "planned": "排练通过", "blocked": "依赖阻塞", "unavailable": "不可用", "rolled_back": "已回滚"}


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
<section><h2>产物与校验值</h2><p>以下路径相对于项目目录。历史副本在后续运行中保留。</p>{files}</section>
<section><h2>事件时间线</h2>{timeline}</section>
<footer><small>SimLoom AI · {e(report['schema_version'])} · 可离线阅读，也可通过浏览器打印为 PDF。</small></footer>
</main></body></html>'''

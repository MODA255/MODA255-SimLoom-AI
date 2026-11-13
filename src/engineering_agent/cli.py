from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import EngineeringAgent
from .queue import TaskQueue
from .tasks import TaskRunner, input_snapshot
from .results import MetricRule
from .web import serve
from .mcp_server import serve_stdio
from .validation import validate_plan
from .windows_bridge import serve_bridge
from .brep import brep_doctor


def _default_config() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "engineering-agent.example.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engineering-agent", description="Ansys/CATIA/Adams/VI-grade engineering agent")
    parser.add_argument("--config", default=str(_default_config()))
    parser.add_argument("--project-dir", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    for action in ("history", "undo", "redo"):
        command = sub.add_parser("cad-" + action, help="CAD 持久历史 / 撤销 / 重做")
        command.add_argument("input_file")
    plot = sub.add_parser("cad-plot", help="将 DXF 工程图导出为 PDF / PNG")
    plot.add_argument("input_file")
    plot.add_argument("output_file")
    plot.add_argument("--layout", default="Model")
    plot.add_argument("--paper", choices=["A4", "A3", "A2", "A1", "A0", "layout"])
    plot.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape")
    plot.add_argument("--scale", help="fit 或比例，如 1:2")
    plot.add_argument("--drawing-units", choices=["mm", "cm", "m", "in", "ft"])
    plot.add_argument("--dpi", type=int, default=150)
    plot.add_argument("--margin-mm", type=float, default=10)
    plot.add_argument("--color-mode", choices=["monochrome", "color"], default="monochrome")
    plot.add_argument("--overwrite", action="store_true")
    plan = sub.add_parser("plan", help="根据自然语言生成工具链计划")
    plan.add_argument("prompt")
    plan.add_argument("--parameters", default="{}", help="工程参数 JSON，例如工况、载荷或 model_spec")
    preflight = sub.add_parser("preflight", help="检查软件动作、脚本、启动命令和输出契约，不启动外部软件")
    preflight.add_argument("prompt")
    preflight.add_argument("--parameters", default="{}", help="工程参数 JSON")
    preflight.add_argument("--probe", action="store_true", help="同时探测已配置的远程 worker")
    run = sub.add_parser("run", help="执行计划；默认仅 dry-run")
    run.add_argument("prompt")
    run.add_argument("--parameters", default="{}", help="传递给软件步骤的工程参数 JSON")
    run.add_argument("--execute", action="store_true", help="允许调用已配置的外部工具")
    workflow = sub.add_parser("workflow", help="查看或恢复已有工程链；恢复会继续剩余的软件操作")
    workflow.add_argument("action", choices=["list", "status", "resume", "repair"])
    workflow.add_argument("workflow_id", nargs="?")
    sub.add_parser("doctor", help="检查四个工具的连接配置")
    job = sub.add_parser("software-job", help="查看、取消或回收已有远程软件任务，不重新启动求解")
    job.add_argument("tool", choices=["catia", "ansys", "adams", "vi-grade"])
    job.add_argument("action", choices=["list", "receipts", "status", "logs", "events", "cancel", "release", "collect"])
    job.add_argument("request_id", nargs="?")
    job.add_argument("--stream", choices=["stdout", "stderr"])
    job.add_argument("--offset", type=int)
    job.add_argument("--after", type=int)
    job.add_argument("--cursor", type=int)
    job.add_argument("--limit", type=int)
    job.add_argument("--confirmed-stopped", action="store_true")
    job.add_argument("--reason")
    windows_validate = sub.add_parser("windows-validate", help="检查软件配置；可选探测显式配置的 COM 对象")
    windows_validate.add_argument("--smoke", action="store_true", help="显式执行 COM dispatch 探针")
    sub.add_parser("brep-doctor", help="检查 OpenCascade/OCP B-Rep 后端")
    brep_inspect = sub.add_parser("brep-inspect", help="检查 STEP solid 的 B-Rep 属性")
    brep_inspect.add_argument("input_file")
    brep_primitive = sub.add_parser("brep-primitive", help="创建 OpenCascade box/cylinder/sphere/cone STEP solid")
    brep_primitive.add_argument("primitive", choices=["box", "cylinder", "sphere", "cone"])
    brep_primitive.add_argument("output_file")
    brep_primitive.add_argument("--parameters", default="{}", help="JSON object")
    brep_build = sub.add_parser("brep-build", help="按参数和约束构建 STEP feature graph")
    brep_build.add_argument("features_file", help="JSON 文件：features 数组或包含 features 的对象")
    brep_build.add_argument("output_file")
    brep_build.add_argument("--parameters", default="{}", help="JSON object")
    brep_build.add_argument("--constraints", default="[]", help="JSON array")
    brep_boolean = sub.add_parser("brep-boolean", help="对两个 STEP solid 执行 fuse/cut/common")
    brep_boolean.add_argument("operation", choices=["fuse", "cut", "common"])
    brep_boolean.add_argument("left_file")
    brep_boolean.add_argument("right_file")
    brep_boolean.add_argument("output_file")
    generate = sub.add_parser("generate", help="生成四个软件的模型规格和脚本")
    generate.add_argument("--name", default="vehicle_model")
    offline = sub.add_parser("offline-run", help="运行无需商业软件的四软件离线工程链")
    offline.add_argument("--model-spec", default="{}", help="JSON model spec overrides")
    offline.add_argument("--rules", default="", help="Optional JSON metric rules file")
    evaluate = sub.add_parser("evaluate", help="解析结果文件并判断工程指标")
    evaluate.add_argument("result_file")
    evaluate.add_argument("rules_file", help="JSON 数组: name/path/operator/target/unit")
    serve_cmd = sub.add_parser("serve", help="启动本地 Web 控制台")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8787)
    serve_cmd.add_argument("--db", default="engineering-agent.db")
    desktop = sub.add_parser("desktop", help="启动本地工作台；可选独立桌面窗口")
    desktop.add_argument("--mode", choices=["auto", "browser", "native"], default="auto")
    desktop.add_argument("--data-dir")
    desktop.add_argument("--port", type=int, default=0)
    enqueue = sub.add_parser("enqueue", help="将任务写入 SQLite 队列")
    enqueue.add_argument("prompt")
    enqueue.add_argument("--db", default="engineering-agent.db")
    enqueue.add_argument("--project-id")
    enqueue.add_argument("--parameters", default="{}", help="保存到任务快照的软件工程参数 JSON")
    worker = sub.add_parser("worker", help="执行 SQLite 队列中的任务")
    worker.add_argument("--db", default="engineering-agent.db")
    worker.add_argument("--execute", action="store_true")
    worker.add_argument("--once", action="store_true")
    project_create = sub.add_parser("project-create", help="创建历史项目")
    project_create.add_argument("name")
    project_create.add_argument("--db", default="engineering-agent.db")
    projects = sub.add_parser("projects", help="列出历史项目")
    projects.add_argument("--db", default="engineering-agent.db")
    mcp = sub.add_parser("mcp", help="启动 stdio MCP tool server")
    mcp.add_argument("--profile", choices=["safe", "full"], default="safe")
    validate = sub.add_parser("validate", help="校验自然语言生成的执行计划")
    validate.add_argument("prompt")
    validate.add_argument("--parameters", default="{}", help="校验计划时使用的工程参数 JSON")
    ingest = sub.add_parser("ingest", help="读取 STEP/CSV/JSON 工程文件")
    ingest.add_argument("path")
    ingest.add_argument("--kind")
    inspect = sub.add_parser("inspect", help="读取项目目录中的工程资产")
    inspect.add_argument("--root", default=".")
    version = sub.add_parser("version", help="创建工程资产版本快照")
    version.add_argument("label")
    version.add_argument("paths", nargs="+")
    version.add_argument("--root", default=".")
    version.add_argument("--parent")
    version_export = sub.add_parser("version-export", help="将原始文件版本导出到新的项目子文件夹")
    version_export.add_argument("version_id")
    version_export.add_argument("output_dir")
    version_export.add_argument("--root", default=".")
    diff = sub.add_parser("diff", help="比较两个工程资产版本")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--root", default=".")
    watch_start = sub.add_parser("watch-start", help="记录外部工程文件的基线并开始可恢复监测")
    watch_start.add_argument("paths", nargs="+", help="项目内文件或目录")
    watch_start.add_argument("--root", default=".")
    watch_start.add_argument("--label", default="")
    watch_poll = sub.add_parser("watch-poll", help="轮询工程文件变化")
    watch_poll.add_argument("watch_id")
    watch_poll.add_argument("--root", default=".")
    watch_poll.add_argument("--since", type=int)
    watch_status = sub.add_parser("watch-status", help="查看工程文件监测状态")
    watch_status.add_argument("watch_id")
    watch_status.add_argument("--root", default=".")
    watch_stop = sub.add_parser("watch-stop", help="停止工程文件监测并保留历史")
    watch_stop.add_argument("watch_id")
    watch_stop.add_argument("--root", default=".")
    dependencies = sub.add_parser("dependencies", help="扫描工程文件之间的可审计依赖")
    dependencies.add_argument("paths", nargs="*", help="可选的项目内文件或目录")
    dependencies.add_argument("--root", default=".")
    write = sub.add_parser("write", help="将 JSON payload 写入 STEP/CSV/JSON/DXF/OBJ/SVG/PDF")
    write.add_argument("format", choices=["step", "csv", "json", "dxf", "obj", "svg", "pdf"])
    write.add_argument("path")
    write.add_argument("payload", help="JSON 文件路径")
    bridge = sub.add_parser("bridge", help="启动 Windows 工具桥接服务")
    bridge.add_argument("--bridge-config", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    agent = EngineeringAgent(EngineeringAgent.load_config(args.config))
    if args.command == "workflow":
        result = agent.workflow(args.action, args.workflow_id, project_dir=args.project_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.action in {"list", "status"} or result.get("status") in {"committed", "needs_review"} else 2
    if args.command == "software-job":
        options = {key: getattr(args, key) for key in ("stream", "offset", "after", "cursor", "limit", "reason", "confirmed_stopped")
                   if getattr(args, key) is not None}
        result = agent.software_job(args.tool, args.action, args.request_id, project_dir=args.project_dir, **options)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "cad-plot":
        arguments = {key: value for key, value in vars(args).items() if key not in {"command", "config"} and value is not None}
        print(json.dumps(agent.execute_operation("cad.plot", arguments), ensure_ascii=False, indent=2))
        return 0
    if args.command == "desktop":
        from .desktop import launch
        return launch(agent, data_dir=args.data_dir, mode=args.mode, port=args.port)
    if args.command == "doctor":
        print(json.dumps(agent.doctor(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "preflight":
        result = agent.preflight(args.prompt, project_dir=args.project_dir,
                                 parameters=json.loads(args.parameters), probe=args.probe)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ready") else 2
    if args.command == "windows-validate":
        result = agent.validate_windows_backends(smoke=args.smoke)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready_count"] == 4 else 2
    if args.command == "brep-doctor":
        print(json.dumps(brep_doctor(), ensure_ascii=False, indent=2))
        return 0 if brep_doctor()["available"] else 2
    if args.command == "brep-inspect":
        print(json.dumps(agent.execute_operation("brep.inspect_step", {"input_file": args.input_file}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "brep-primitive":
        print(json.dumps(agent.execute_operation("brep.create_primitive", {"primitive": args.primitive, "output_file": args.output_file, "project_dir": args.project_dir, "parameters": json.loads(args.parameters)}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "brep-build":
        payload = json.loads(Path(args.features_file).read_text(encoding="utf-8"))
        features = payload.get("features", payload) if isinstance(payload, dict) else payload
        print(json.dumps(agent.execute_operation("brep.build_feature_graph", {"features": features, "output_file": args.output_file, "project_dir": args.project_dir, "parameters": json.loads(args.parameters), "constraints": json.loads(args.constraints)}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "brep-boolean":
        print(json.dumps(agent.execute_operation("brep.boolean", {"operation": args.operation, "left_file": args.left_file, "right_file": args.right_file, "output_file": args.output_file, "project_dir": args.project_dir}), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"cad-history", "cad-undo", "cad-redo"}:
        result = agent.execute_operation(args.command.replace("cad-", "cad."), {
            "input_file": args.input_file, "project_dir": args.project_dir})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "mcp":
        serve_stdio(agent, args.profile)
        return 0
    if args.command == "validate":
        print(json.dumps(validate_plan(agent.plan(args.prompt, project_dir=args.project_dir, parameters=json.loads(args.parameters))), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest":
        print(json.dumps(agent.ingest(args.path, args.kind), ensure_ascii=False, indent=2))
        return 0
    if args.command == "inspect":
        print(json.dumps(agent.inspect_project(args.root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "version":
        print(json.dumps(agent.create_version(args.root, args.label, [str(Path(path).expanduser().absolute()) for path in args.paths], args.parent), ensure_ascii=False, indent=2))
        return 0
    if args.command == "version-export":
        print(json.dumps(agent.execute_operation("project.version_export", {"project_dir": args.root,
            "version_id": args.version_id, "output_dir": args.output_dir}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "diff":
        print(json.dumps(agent.diff_versions(args.root, args.left, args.right), ensure_ascii=False, indent=2))
        return 0
    if args.command == "watch-start":
        print(json.dumps(agent.execute_operation("project.watch_start", {"project_dir": args.root, "paths": args.paths, "label": args.label}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "watch-poll":
        arguments = {"project_dir": args.root, "watch_id": args.watch_id}
        if args.since is not None:
            arguments["since"] = args.since
        print(json.dumps(agent.execute_operation("project.watch_poll", arguments), ensure_ascii=False, indent=2))
        return 0
    if args.command == "watch-status":
        print(json.dumps(agent.execute_operation("project.watch_status", {"project_dir": args.root, "watch_id": args.watch_id}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "watch-stop":
        print(json.dumps(agent.execute_operation("project.watch_stop", {"project_dir": args.root, "watch_id": args.watch_id}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "dependencies":
        print(json.dumps(agent.execute_operation("project.dependency_scan", {"project_dir": args.root, "paths": args.paths or None}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "write":
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        print(json.dumps({"path": agent.write_asset(args.path, args.format, payload)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "bridge":
        bridge_config = EngineeringAgent.load_config(args.bridge_config)
        serve_bridge(bridge_config.get("windows_bridge", bridge_config))
        return 0
    if args.command == "generate":
        print(json.dumps(agent.generate(args.project_dir, {"name": args.name}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "offline-run":
        rules = json.loads(Path(args.rules).read_text(encoding="utf-8")) if args.rules else None
        result = agent.run_offline_vehicle_chain(args.project_dir, json.loads(args.model_spec), rules)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(item["passed"] for item in result["verdicts"]) else 2
    if args.command == "evaluate":
        rules = [MetricRule(**item) for item in json.loads(Path(args.rules_file).read_text(encoding="utf-8"))]
        verdicts = agent.evaluate(args.result_file, rules)
        print(json.dumps(verdicts, ensure_ascii=False, indent=2))
        return 0 if all(item["passed"] for item in verdicts) else 2
    if args.command == "serve":
        serve(agent, TaskQueue(args.db), args.host, args.port)
        return 0
    if args.command == "enqueue":
        queue = TaskQueue(args.db)
        project = queue.get_project(args.project_id) if args.project_id else None
        if args.project_id and not project:
            raise ValueError("项目不存在")
        root = (project or {}).get("project_dir") or args.project_dir
        task_id = queue.submit(args.prompt, root, args.project_id, inputs=input_snapshot((project or {}).get("metadata"), {"parameters": json.loads(args.parameters)}))
        print(json.dumps(queue.get(task_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "worker":
        queue = TaskQueue(args.db)
        processed = 0
        while True:
            task = queue.next_queued()
            if not task:
                break
            try:
                TaskRunner(agent, queue).run(task["id"], execute=args.execute)
            except RuntimeError as exc:
                if str(exc) == "TASK_ALREADY_RUNNING_OR_FINISHED":
                    continue
                raise
            processed += 1
            if args.once:
                break
        print(json.dumps({"processed": processed, "remaining": len([item for item in queue.list() if item["status"] == "queued"])}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "project-create":
        queue = TaskQueue(args.db)
        project_id = queue.create_project(args.name)
        print(json.dumps({"id": project_id, "name": args.name}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "projects":
        print(json.dumps(TaskQueue(args.db).list_projects(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "plan":
        print(json.dumps(agent.plan(args.prompt, project_dir=args.project_dir, parameters=json.loads(args.parameters)).to_dict(), ensure_ascii=False, indent=2))
        return 0
    report = agent.run(args.prompt, project_dir=args.project_dir, parameters=json.loads(args.parameters), dry_run=not args.execute)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.status == "committed" and all(result.status in {"planned", "completed"} for result in report.results) else 2


if __name__ == "__main__":
    raise SystemExit(main())

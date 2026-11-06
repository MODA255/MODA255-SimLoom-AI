from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import EngineeringAgent
from .queue import TaskQueue
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
    plan = sub.add_parser("plan", help="根据自然语言生成工具链计划")
    plan.add_argument("prompt")
    run = sub.add_parser("run", help="执行计划；默认仅 dry-run")
    run.add_argument("prompt")
    run.add_argument("--execute", action="store_true", help="允许调用已配置的外部工具")
    sub.add_parser("doctor", help="检查四个工具的连接配置")
    windows_validate = sub.add_parser("windows-validate", help="在 Windows 主机上验证四个软件的 COM 可用性")
    windows_validate.add_argument("--smoke", action="store_true", help="显式执行 COM dispatch 探针")
    sub.add_parser("brep-doctor", help="检查 OpenCascade/OCP B-Rep 后端")
    brep_inspect = sub.add_parser("brep-inspect", help="检查 STEP solid 的 B-Rep 属性")
    brep_inspect.add_argument("input_file")
    brep_primitive = sub.add_parser("brep-primitive", help="创建 OpenCascade box/cylinder STEP solid")
    brep_primitive.add_argument("primitive", choices=["box", "cylinder"])
    brep_primitive.add_argument("output_file")
    brep_primitive.add_argument("--parameters", default="{}", help="JSON object")
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
    diff = sub.add_parser("diff", help="比较两个工程资产版本")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--root", default=".")
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
    if args.command == "desktop":
        from .desktop import launch
        return launch(agent, data_dir=args.data_dir, mode=args.mode, port=args.port)
    if args.command == "doctor":
        print(json.dumps(agent.doctor(), ensure_ascii=False, indent=2))
        return 0
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
    if args.command == "brep-boolean":
        print(json.dumps(agent.execute_operation("brep.boolean", {"operation": args.operation, "left_file": args.left_file, "right_file": args.right_file, "output_file": args.output_file, "project_dir": args.project_dir}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "mcp":
        serve_stdio(agent, args.profile)
        return 0
    if args.command == "validate":
        print(json.dumps(validate_plan(agent.plan(args.prompt, project_dir=args.project_dir)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest":
        print(json.dumps(agent.ingest(args.path, args.kind), ensure_ascii=False, indent=2))
        return 0
    if args.command == "inspect":
        print(json.dumps(agent.inspect_project(args.root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "version":
        print(json.dumps(agent.create_version(args.root, args.label, args.paths, args.parent), ensure_ascii=False, indent=2))
        return 0
    if args.command == "diff":
        print(json.dumps(agent.diff_versions(args.root, args.left, args.right), ensure_ascii=False, indent=2))
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
        task_id = queue.submit(args.prompt, args.project_dir, args.project_id)
        print(json.dumps(queue.get(task_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "worker":
        queue = TaskQueue(args.db)
        processed = 0
        while True:
            task = queue.next_queued()
            if not task:
                break
            queue.update(task["id"], "running")
            report = agent.run(task["prompt"], project_dir=task["project_dir"], dry_run=not args.execute)
            failed = any(result.status in {"failed", "blocked", "unavailable"} for result in report.results)
            queue.update(task["id"], "failed" if failed else "completed", report.to_dict())
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
        print(json.dumps(agent.plan(args.prompt, project_dir=args.project_dir).to_dict(), ensure_ascii=False, indent=2))
        return 0
    report = agent.run(args.prompt, project_dir=args.project_dir, dry_run=not args.execute)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if all(result.status not in {"failed", "blocked", "unavailable"} for result in report.results) else 2


if __name__ == "__main__":
    raise SystemExit(main())

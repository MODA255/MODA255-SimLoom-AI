from __future__ import annotations

import json
import mimetypes
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .agent import EngineeringAgent
from .queue import TaskQueue
from .validation import validate_plan
from .viewer import artifact_listing, artifact_preview, asset_view, render_asset_svg, render_timeline_svg
from .versioning import VersionStore
from .workspace import TEMPLATES, Workspace
from .tasks import TaskRunner, input_snapshot
from .reports import build_report, render_report
from .worker_protocol import WorkerError

STATIC_DIR = Path(__file__).with_name("static")
INDEX_HTML = (STATIC_DIR / "workspace.html").read_text(encoding="utf-8")
WEB_SCHEMA_VERSION = "simloom-ai/web/v1"


def create_server(agent: EngineeringAgent, queue: TaskQueue, host: str = "127.0.0.1", port: int = 8787,
                  workspace_root: str | Path | None = None) -> ThreadingHTTPServer:
    """Construct the same local service for browsers, desktop windows and tests."""
    workspace = Workspace(queue, workspace_root)
    runner = TaskRunner(agent, queue)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any, content_type: str = "application/json", filename=None):
            if isinstance(payload, bytes):
                body = payload
            elif isinstance(payload, str):
                body = payload.encode("utf-8")
            else:
                body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8\'\'" + quote(filename))
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求必须使用 application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_048_576:
                raise ValueError("请求大小必须在 1 字节到 1 MB 之间")
            def invalid_constant(value):
                raise ValueError("JSON 中不能包含 " + value)
            body = json.loads(self.rfile.read(length), parse_constant=invalid_constant)
            if not isinstance(body, dict):
                raise ValueError("请求内容必须为 JSON 对象")
            return body

        def _guard_origin(self):
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                raise PermissionError("只允许工作台页面发起此请求")

        def _handle(self, callback):
            try:
                self._guard_origin()
                callback()
            except (BrokenPipeError, ConnectionResetError):
                return
            except FileNotFoundError as exc:
                self._send(404, {"error": str(exc), "code": "RESOURCE_NOT_FOUND"})
            except FileExistsError as exc:
                self._send(409, {"error": str(exc), "code": "PROJECT_EXISTS"})
            except PermissionError as exc:
                self._send(403, {"error": str(exc), "code": "ACCESS_DENIED"})
            except WorkerError as exc:
                status = 409 if exc.code in {"PROJECT_OUTPUT_CHANGED", "PUBLISHED_RESULT_CHANGED", "JOB_STILL_ACTIVE", "JOB_NOT_COMPLETED", "JOB_ID_CONFLICT"} else 400
                self._send(status, {"error": str(exc), "code": exc.code})
            except (ValueError, TypeError, KeyError) as exc:
                self._send(400, {"error": str(exc), "code": "INVALID_ARGUMENTS"})
            except RuntimeError as exc:
                locked = "LOCKED" in str(exc) or "ALREADY_RUNNING" in str(exc)
                conflict = "CONFLICT" in str(exc)
                self._send(409 if locked or conflict else 500, {"error": str(exc), "code": "BUSY" if locked else "CONFLICT" if conflict else "RUNTIME_ERROR"})
            except Exception as exc:
                self._send(500, {"error": str(exc), "code": "INTERNAL_ERROR"})

        def do_GET(self):
            self._handle(self._get)

        def do_POST(self):
            self._handle(self._post)

        def _project_input(self, body):
            project_id = body.get("project_id")
            if project_id:
                project = queue.get_project(project_id)
                if not project or not project.get("project_dir"):
                    raise FileNotFoundError("项目不存在，或旧项目尚未关联目录")
                return project["project_dir"], project["metadata"], project_id
            root = body.get("project_dir", ".")
            if not isinstance(root, str) or not root.strip():
                raise ValueError("请填写有效项目目录")
            return str(Path(root).expanduser().resolve()), {}, None

        def _get(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            root = query.get("root", ["."])[0]
            relative = query.get("path", [""])[0]
            if parsed.path == "/":
                return self._send(200, INDEX_HTML, "text/html")
            static = {"/static/workspace.css": ("workspace.css", "text/css"),
                      "/static/workspace.js": ("workspace.js", "text/javascript")}
            if parsed.path in static:
                filename, mime = static[parsed.path]
                return self._send(200, (STATIC_DIR / filename).read_bytes(), mime)
            if parsed.path == "/api/health":
                return self._send(200, {"ok": True})
            if parsed.path == "/api/workspace":
                return self._send(200, {"projects_root": str(workspace.root), "schemaVersion": WEB_SCHEMA_VERSION,
                                       "planner": "llm-with-fallback" if agent.config.get("llm", {}).get("enabled") else "deterministic"})
            if parsed.path == "/api/templates":
                return self._send(200, {"templates": TEMPLATES, "schemaVersion": WEB_SCHEMA_VERSION})
            if parsed.path == "/api/doctor":
                return self._send(200, agent.doctor())
            if parsed.path == "/api/capabilities":
                return self._send(200, agent.capabilities())
            if parsed.path == "/api/tasks":
                tasks = queue.list(project_id=query.get("project_id", [None])[0])
                return self._send(200, tasks)
            if parsed.path == "/api/projects":
                return self._send(200, queue.list_projects())
            if parsed.path == "/api/versions":
                project_id = query.get("project_id", [None])[0]
                if project_id:
                    project = queue.get_project(project_id)
                    if not project or not project.get("project_dir"):
                        raise FileNotFoundError("项目不存在，或旧项目尚未关联目录")
                    root = project["project_dir"]
                snapshots = VersionStore(root).list()
                versions = [{
                    "id": snapshot.get("id"),
                    "label": snapshot.get("label"),
                    "parent": snapshot.get("parent"),
                    "created_at": snapshot.get("created_at"),
                    "object_count": len(snapshot.get("objects", [])),
                    "has_dxf": any(
                        str((asset.get("geometry") or {}).get("format", "")).upper() == "DXF"
                        for asset in snapshot.get("objects", [])
                    ),
                } for snapshot in snapshots]
                events = VersionStore(root).timeline()
                return self._send(200, {"versions": versions, "events": events,
                                       "svg": render_timeline_svg(events),
                                       "schemaVersion": WEB_SCHEMA_VERSION})
            if parsed.path == "/api/version-view":
                project_id = query.get("project_id", [None])[0]
                if project_id:
                    project = queue.get_project(project_id)
                    if not project or not project.get("project_dir"):
                        raise FileNotFoundError("项目不存在，或旧项目尚未关联目录")
                    root = project["project_dir"]
                version_id = query.get("version_id", [""])[0]
                if not version_id:
                    raise ValueError("请指定版本")
                snapshot = VersionStore(root).load(version_id)
                objects = snapshot.get("objects", [])
                if not objects:
                    raise ValueError("VERSION_EMPTY")
                try:
                    asset_index = int(query.get("asset_index", ["0"])[0])
                except ValueError as exc:
                    raise ValueError("asset_index 必须是整数") from exc
                if asset_index < 0 or asset_index >= len(objects):
                    raise ValueError("VERSION_ASSET_NOT_FOUND")
                asset = objects[asset_index]
                geometry = asset.get("geometry") or {}
                svg = render_asset_svg({"geometry": geometry, "metrics": asset.get("metrics") or {}})
                assets = [{"index": index, "name": item.get("name", ""), "kind": item.get("kind", "unknown"),
                           "path": (item.get("source") or {}).get("path", ""),
                           "format": (item.get("geometry") or {}).get("format", "")}
                          for index, item in enumerate(objects)]
                return self._send(200, {"version": {key: snapshot.get(key) for key in ("id", "label", "parent", "created_at")},
                                       "asset": {"index": asset_index, "name": asset.get("name", ""),
                                                 "kind": asset.get("kind", "unknown"), "source": asset.get("source"),
                                                 "geometry": {"format": geometry.get("format"), "entity_count": len(geometry.get("entities", [])) if isinstance(geometry.get("entities"), list) else None}},
                                       "assets": assets, "svg": svg, "schemaVersion": WEB_SCHEMA_VERSION})
            if parsed.path.startswith("/api/projects/"):
                project = queue.get_project(parsed.path[len("/api/projects/"):])
                if not project:
                    raise FileNotFoundError("项目不存在")
                return self._send(200, project)
            if parsed.path.startswith("/api/tasks/"):
                task_path = parsed.path[len("/api/tasks/"):]
                if task_path.endswith("/report"):
                    task_id = task_path[:-len("/report")]
                    report = build_report(queue, task_id)
                    format_name = query.get("format", ["json"])[0]
                    if format_name not in {"html", "json"}:
                        raise ValueError("报告格式仅支持 html 或 json")
                    filename = f"simloom-{task_id}.{format_name}" if query.get("download") == ["1"] else None
                    return self._send(200, render_report(report) if format_name == "html" else report,
                                      "text/html" if format_name == "html" else "application/json", filename)
                if task_path.endswith("/events"):
                    if not queue.get(task_path[:-len("/events")]):
                        raise FileNotFoundError("任务不存在")
                    return self._send(200, queue.events(task_path[:-len("/events")]))
                task = queue.get(task_path)
                if not task:
                    raise FileNotFoundError("任务不存在")
                return self._send(200, task)
            if parsed.path == "/api/artifacts":
                return self._send(200, artifact_listing(root))
            if parsed.path == "/api/artifact":
                return self._send(200, artifact_preview(root, relative))
            if parsed.path == "/api/asset-view":
                return self._send(200, asset_view(root, relative))
            if parsed.path == "/api/cad/history-view":
                return self._send(200, agent.execute_operation("cad.history_view", {
                    "project_dir": root, "input_file": relative,
                    "revision_id": query.get("revision_id", [""])[0],
                    "side": query.get("side", ["after"])[0]}))
            if parsed.path == "/api/download":
                base = Path(root).resolve()
                path = (base / relative).resolve()
                if base not in path.parents:
                    raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
                if not path.is_file():
                    raise FileNotFoundError(relative)
                with path.open("rb") as stream:
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    shutil.copyfileobj(stream, self.wfile)
                return
            if parsed.path == "/api/timeline":
                events = VersionStore(root).timeline()
                return self._send(200, {"events": events, "svg": render_timeline_svg(events)})
            self._send(404, {"error": "接口不存在"})

        def _post(self):
            body = self._json()
            path = urlparse(self.path).path
            if path == "/api/workflows":
                root, _metadata, _project_id = self._project_input(body)
                action, identifier = body.get("action", "list"), body.get("workflow_id")
                task = queue.find_workflow(identifier) if action == "resume" and identifier else None
                if task and Path(task["project_dir"]).resolve() == Path(root).resolve():
                    return self._send(200, runner.resume(task["id"]))
                return self._send(200, agent.workflow(action, identifier, project_dir=root))
            if path == "/api/software-jobs":
                root, _metadata, _project_id = self._project_input(body)
                options = {key: body[key] for key in ("limit", "cursor", "offset", "after", "stream", "confirmed_stopped", "reason") if key in body}
                return self._send(200, agent.software_job(body.get("tool", ""), body.get("action", "status"),
                                                        body.get("request_id"), project_dir=root, **options))
            if path == "/api/projects":
                return self._send(201, workspace.create_project(body))
            if path in {"/api/plan", "/api/preflight", "/api/tasks"}:
                root, metadata, project_id = self._project_input(body)
                prompt = body.get("prompt", "")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("请先输入工程目标")
                if path == "/api/plan":
                    snapshot = input_snapshot(metadata, body)
                    plan = agent.plan(prompt, project_dir=root, parameters={**snapshot["parameters"], "model_spec": snapshot["model_spec"]})
                    return self._send(200, {"plan": plan.to_dict(), "validation": validate_plan(plan)})
                if path == "/api/preflight":
                    snapshot = input_snapshot(metadata, body)
                    return self._send(200, agent.preflight(
                        prompt, project_dir=root,
                        parameters={**snapshot["parameters"], "model_spec": snapshot["model_spec"]},
                        probe=bool(body.get("probe", False))))
                task_id = queue.submit(prompt, root, project_id, inputs=input_snapshot(metadata, body))
                return self._send(201, queue.get(task_id))
            if path == "/api/offline-run":
                root, metadata, project_id = self._project_input(body)
                task_id = queue.submit("四软件离线演示", root, project_id, mode="offline",
                                       inputs=input_snapshot(metadata, body))
                return self._send(200, runner.run(task_id))
            if path == "/api/cad/operation":
                operation = body.get("operation")
                arguments = body.get("arguments") or {key: value for key, value in body.items() if key != "operation"}
                if not isinstance(operation, str) or not operation.startswith("cad."):
                    raise ValueError("CAD operation must use the cad.* namespace")
                return self._send(200, agent.execute_operation(operation, arguments))
            if path == "/api/version-visual-diff":
                root, _metadata, _project_id = self._project_input(body)
                left, right = body.get("left"), body.get("right")
                if not isinstance(left, str) or not left.strip() or not isinstance(right, str) or not right.strip():
                    raise ValueError("请同时选择左侧和右侧版本")
                arguments = {
                    "project_dir": root,
                    "left": left,
                    "right": right,
                    "width": body.get("width", 900),
                    "height": body.get("height", 560),
                }
                return self._send(200, agent.execute_operation("project.visual_diff", arguments))
            if path == "/api/version":
                root, _metadata, _project_id = self._project_input(body)
                label = body.get("label")
                if not isinstance(label, str) or not label.strip() or len(label.strip()) > 160:
                    raise ValueError("版本名称必须是 1 到 160 个字符")
                requested = body.get("paths")
                if requested is None:
                    requested = [item["path"] for item in artifact_listing(root)
                                 if item.get("suffix", "").lower() in {".dxf", ".step", ".stp", ".csv", ".json"}
                                 and not str(item.get("path", "")).endswith(".cad.json")
                                 and Path(str(item.get("path", ""))).name != "simloom.project.json"]
                if not isinstance(requested, list) or not requested:
                    raise ValueError("项目中没有可纳入快照的工程资产")
                root_path = Path(root).resolve()
                paths = []
                for relative in requested:
                    if not isinstance(relative, str) or not relative.strip():
                        raise ValueError("快照路径必须是非空字符串")
                    candidate = (root_path / relative).resolve()
                    if root_path not in candidate.parents or not candidate.is_file():
                        raise ValueError("VERSION_PATH_OUTSIDE_PROJECT:" + relative)
                    paths.append(str(candidate))
                parent = body.get("parent")
                if parent is not None and not isinstance(parent, str):
                    raise ValueError("父版本编号无效")
                result = agent.create_version(root, label.strip(), paths, parent)
                return self._send(201, result)
            if path.startswith("/api/tasks/") and path.endswith("/resume"):
                return self._send(200, runner.resume(path[len("/api/tasks/"):-len("/resume")]))
            if path.startswith("/api/tasks/") and path.endswith("/retry"):
                task_id = queue.retry(path[len("/api/tasks/"):-len("/retry")])
                return self._send(201, queue.get(task_id))
            if path.startswith("/api/tasks/") and path.endswith("/run"):
                return self._send(200, runner.run(path[len("/api/tasks/"):-len("/run")]))
            self._send(404, {"error": "接口不存在"})

        def log_message(self, *_args):
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(agent: EngineeringAgent, queue: TaskQueue, host: str = "127.0.0.1", port: int = 8787):
    server = create_server(agent, queue, host, port)
    print(f"SimLoom AI: http://{host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    finally:
        server.server_close()

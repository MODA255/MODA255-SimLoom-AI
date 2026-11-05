from __future__ import annotations

import json
import mimetypes
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .agent import EngineeringAgent
from .queue import TaskQueue
from .validation import validate_plan
from .viewer import artifact_listing, artifact_preview, asset_view, render_timeline_svg
from .versioning import VersionStore
from .workspace import TEMPLATES, Workspace

STATIC_DIR = Path(__file__).with_name("static")
INDEX_HTML = (STATIC_DIR / "workspace.html").read_text(encoding="utf-8")
WEB_SCHEMA_VERSION = "simloom-ai/web/v1"


def create_server(agent: EngineeringAgent, queue: TaskQueue, host: str = "127.0.0.1", port: int = 8787,
                  workspace_root: str | Path | None = None) -> ThreadingHTTPServer:
    """Construct the same local service for browsers, desktop windows and tests."""
    workspace = Workspace(queue, workspace_root)
    busy_projects: set[str] = set()
    busy_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any, content_type: str = "application/json"):
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
            except (ValueError, TypeError, KeyError) as exc:
                self._send(400, {"error": str(exc), "code": "INVALID_ARGUMENTS"})
            except RuntimeError as exc:
                locked = "LOCKED" in str(exc) or "ALREADY_RUNNING" in str(exc)
                self._send(409 if locked else 500, {"error": str(exc), "code": "BUSY" if locked else "RUNTIME_ERROR"})
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
            if parsed.path == "/api/tasks":
                tasks = queue.list()
                if query.get("project_id"):
                    tasks = [item for item in tasks if item["project_id"] == query["project_id"][0]]
                return self._send(200, tasks)
            if parsed.path == "/api/projects":
                return self._send(200, queue.list_projects())
            if parsed.path.startswith("/api/projects/"):
                project = queue.get_project(parsed.path[len("/api/projects/"):])
                if not project:
                    raise FileNotFoundError("项目不存在")
                return self._send(200, project)
            if parsed.path.startswith("/api/tasks/"):
                task_path = parsed.path[len("/api/tasks/"):]
                if task_path.endswith("/events"):
                    return self._send(200, queue.events(task_path[:-len("/events")].rstrip("/")))
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
            if path == "/api/projects":
                return self._send(201, workspace.create_project(body))
            if path in {"/api/plan", "/api/tasks"}:
                root, metadata, project_id = self._project_input(body)
                prompt = body.get("prompt", "")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("请先输入工程目标")
                if path == "/api/plan":
                    plan = agent.plan(prompt, project_dir=root, parameters={"model_spec": metadata.get("model_spec", {})})
                    return self._send(200, {"plan": plan.to_dict(), "validation": validate_plan(plan)})
                task_id = queue.submit(prompt, root, project_id)
                return self._send(201, queue.get(task_id))
            if path == "/api/offline-run":
                root, metadata, project_id = self._project_input(body)
                with busy_lock:
                    if root in busy_projects:
                        raise RuntimeError("PROJECT_ALREADY_RUNNING")
                    busy_projects.add(root)
                task_id = None
                try:
                    task_id = queue.submit("四软件离线演示", root, project_id)
                    queue.update(task_id, "running")
                    result = agent.run_offline_vehicle_chain(root, body.get("model_spec") or metadata.get("model_spec", {}),
                                                            body.get("rules", metadata.get("rules")))
                    result["task_id"] = task_id
                    result["validation_passed"] = bool(result["verdicts"]) and all(v["passed"] for v in result["verdicts"])
                    queue.update(task_id, "completed", result)
                    return self._send(200, result)
                except Exception as exc:
                    if task_id:
                        queue.update(task_id, "failed", {"error": str(exc)})
                    raise
                finally:
                    with busy_lock:
                        busy_projects.discard(root)
            if path.startswith("/api/tasks/") and path.endswith("/run"):
                task_id = path[len("/api/tasks/"):-len("/run")]
                task = queue.get(task_id)
                if not task:
                    raise FileNotFoundError("任务不存在")
                if not queue.claim(task_id):
                    raise RuntimeError("TASK_ALREADY_RUNNING_OR_FINISHED")
                try:
                    project = queue.get_project(task["project_id"]) if task["project_id"] else None
                    spec = (project or {}).get("metadata", {}).get("model_spec", {})
                    result = agent.run(task["prompt"], project_dir=task["project_dir"], parameters={"model_spec": spec}, dry_run=True).to_dict()
                    queue.update(task_id, "completed" if result["status"] == "committed" else "failed", result)
                    return self._send(200, result)
                except Exception as exc:
                    queue.update(task_id, "failed", {"error": str(exc)})
                    raise
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

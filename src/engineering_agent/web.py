from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any

from .agent import EngineeringAgent
from .queue import TaskQueue
from .validation import validate_plan
from .viewer import artifact_listing, artifact_preview, asset_view, render_timeline_svg
from .versioning import VersionStore


INDEX_HTML = """<!doctype html><meta charset='utf-8'><title>Engineering Agent</title>
<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;color:#17202a}textarea{width:100%;height:120px;border:1px solid #ccd;border-radius:6px;padding:10px}button{padding:8px 14px;margin:8px 4px 8px 0;border:0;border-radius:5px;background:#2855c5;color:#fff}pre{background:#f5f7fb;padding:16px;white-space:pre-wrap;overflow:auto}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card{border:1px solid #dde3ee;border-radius:8px;padding:16px}table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #e6e9ef;text-align:left}</style>
<h1>Engineering Agent</h1><p>CATIA · Ansys · Adams · VI-grade</p>
<div class='grid'><div class='card'><textarea id='prompt'>用CATIA建悬架模型，导入Adams做动力学，再用VI-grade做操稳分析</textarea><br>
<button onclick='plan()'>生成计划</button><button onclick='submitTask()'>加入任务队列</button></div><div class='card'><b>任务时间线</b><div id='tasks'>点击刷新查看</div><button onclick='refreshTasks()'>刷新任务</button></div></div>
<pre id='out'></pre>
<script>const out=document.querySelector('#out'); const prompt=()=>document.querySelector('#prompt').value;
async function plan(){let r=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:prompt()})});out.textContent=JSON.stringify(await r.json(),null,2)}
async function submitTask(){let r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:prompt()})});out.textContent=JSON.stringify(await r.json(),null,2);refreshTasks()}
async function refreshTasks(){let r=await fetch('/api/tasks');let items=await r.json();document.querySelector('#tasks').innerHTML='<table><tr><th>Status</th><th>Prompt</th><th>Created</th></tr>'+items.map(x=>'<tr><td>'+x.status+'</td><td>'+x.prompt+'</td><td>'+x.created_at+'</td></tr>').join('')+'</table>'} refreshTasks()</script>"""


def serve(agent: EngineeringAgent, queue: TaskQueue, host: str = "127.0.0.1", port: int = 8787):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any, content_type: str = "application/json"):
            body = payload.encode() if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return self._send(200, INDEX_HTML, "text/html")
            if parsed.path == "/api/health":
                return self._send(200, {"ok": True})
            if parsed.path == "/api/doctor":
                return self._send(200, agent.doctor())
            if parsed.path == "/api/tasks":
                return self._send(200, queue.list())
            if parsed.path == "/api/projects":
                return self._send(200, queue.list_projects())
            if parsed.path.startswith("/api/tasks/"):
                task_path = parsed.path[len("/api/tasks/"):]
                if task_path.endswith("/events"):
                    task_id = task_path[:-len("/events")].rstrip("/")
                    return self._send(200, queue.events(task_id))
                return self._send(200, queue.get(task_path) or {"error": "task not found"})
            if parsed.path == "/api/artifacts":
                query = parse_qs(parsed.query)
                root = query.get("root", ["."])[0]
                return self._send(200, artifact_listing(root))
            if parsed.path == "/api/artifact":
                query = parse_qs(parsed.query)
                root = query.get("root", ["."])[0]
                relative = query.get("path", [""])[0]
                try:
                    return self._send(200, artifact_preview(root, relative))
                except Exception as exc:
                    return self._send(400, {"error": str(exc)})
            if parsed.path == "/api/asset-view":
                query = parse_qs(parsed.query)
                root = query.get("root", ["."])[0]
                relative = query.get("path", [""])[0]
                try:
                    return self._send(200, asset_view(root, relative))
                except Exception as exc:
                    return self._send(400, {"error": str(exc)})
            if parsed.path == "/api/timeline":
                query = parse_qs(parsed.query)
                events = VersionStore(query.get("root", ["."])[0]).timeline()
                return self._send(200, {"events": events, "svg": render_timeline_svg(events)})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            body = self._json()
            if self.path == "/api/plan":
                plan = agent.plan(body.get("prompt", ""), project_dir=body.get("project_dir", "."))
                return self._send(200, {"plan": plan.to_dict(), "validation": validate_plan(plan)})
            if self.path == "/api/tasks":
                task_id = queue.submit(body.get("prompt", ""), body.get("project_dir", "."), body.get("project_id"))
                return self._send(201, queue.get(task_id))
            if self.path == "/api/projects":
                project_id = queue.create_project(body.get("name", "unnamed-project"))
                return self._send(201, {"id": project_id, "name": body.get("name", "unnamed-project")})
            return self._send(404, {"error": "not found"})

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Engineering Agent web UI: http://{host}:{port}")
    server.serve_forever()

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


INDEX_HTML = """<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>SimLoom AI · Engineering Workspace</title>
<style>
:root{--ink:#172033;--muted:#718096;--line:#e5eaf2;--panel:#fff;--bg:#f5f7fb;--blue:#3d62ff;--blue-dark:#2949cf;--mint:#1ea97b;--amber:#c98518;--danger:#c94c5c;--shadow:0 18px 50px rgba(30,48,90,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,textarea{font:inherit}button{cursor:pointer;border:0}.shell{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}.sidebar{background:#111b33;color:#dce5ff;padding:24px 18px;display:flex;flex-direction:column;gap:26px}.brand{display:flex;align-items:center;gap:11px;color:#fff;font-weight:750;font-size:18px;letter-spacing:-.02em}.brand-mark{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,#86a0ff,#3d62ff);box-shadow:0 8px 18px rgba(61,98,255,.35)}.nav{display:grid;gap:6px}.nav button{background:transparent;color:#b9c6e7;text-align:left;padding:11px 12px;border-radius:10px;display:flex;gap:10px;align-items:center}.nav button:hover,.nav button.active{background:rgba(127,153,255,.16);color:#fff}.side-note{margin-top:auto;border:1px solid rgba(187,202,244,.16);border-radius:14px;padding:14px;color:#aebbd9;font-size:12px}.side-note strong{display:block;color:#fff;font-size:13px;margin-bottom:4px}.main{min-width:0;padding:28px 32px 44px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}.eyebrow{color:var(--blue);font-size:12px;font-weight:750;letter-spacing:.14em;text-transform:uppercase}.topbar h1{font-size:28px;line-height:1.15;letter-spacing:-.04em;margin:5px 0 0}.health{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}.dot{width:8px;height:8px;border-radius:50%;background:var(--mint);box-shadow:0 0 0 4px rgba(30,169,123,.12)}.hero{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,.8fr);gap:20px;margin-bottom:20px}.hero-card{background:linear-gradient(135deg,#1d2b56,#314eaa 60%,#6180e8);border-radius:22px;padding:28px;color:#fff;min-height:238px;position:relative;overflow:hidden;box-shadow:var(--shadow)}.hero-card:after{content:'';position:absolute;width:280px;height:280px;border:1px solid rgba(255,255,255,.15);border-radius:50%;right:-85px;top:-120px;box-shadow:0 0 0 35px rgba(255,255,255,.04),0 0 0 70px rgba(255,255,255,.03)}.hero-card h2{font-size:27px;line-height:1.18;letter-spacing:-.04em;margin:12px 0 9px;max-width:470px;position:relative;z-index:1}.hero-card p{color:#dbe4ff;max-width:530px;margin:0;position:relative;z-index:1}.hero-actions{display:flex;gap:9px;margin-top:24px;position:relative;z-index:1}.btn{border-radius:10px;padding:10px 14px;font-weight:700;transition:.18s transform,.18s background}.btn:hover{transform:translateY(-1px)}.btn-primary{background:#fff;color:#2949cf}.btn-primary:hover{background:#edf1ff}.btn-ghost{background:rgba(255,255,255,.12);color:#fff;border:1px solid rgba(255,255,255,.24)}.btn-blue{background:var(--blue);color:#fff}.btn-blue:hover{background:var(--blue-dark)}.btn-light{background:#f0f3fa;color:#44516b}.hero-stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:var(--shadow)}.stat span{display:block;color:var(--muted);font-size:12px}.stat strong{display:block;font-size:25px;letter-spacing:-.04em;margin-top:4px}.layout{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(300px,.75fr);gap:20px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 8px 25px rgba(30,48,90,.035);min-width:0}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:16px}.card-head h3{margin:0;font-size:16px;letter-spacing:-.02em}.card-head p{margin:4px 0 0;color:var(--muted);font-size:12px}.templates{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}.template{background:#f7f9fd;border:1px solid var(--line);border-radius:12px;padding:12px;text-align:left;color:var(--ink);min-height:88px}.template:hover,.template.selected{border-color:#8fa2ff;background:#f0f3ff}.template-icon{font-size:19px}.template strong{display:block;font-size:12px;margin-top:8px}.template span{display:block;color:var(--muted);font-size:11px;margin-top:2px}.field{display:grid;gap:7px;margin-top:16px}.field label{font-weight:700;font-size:12px}.field input,.field textarea{width:100%;border:1px solid var(--line);border-radius:11px;background:#fbfcfe;padding:11px 12px;color:var(--ink);outline:none}.field input:focus,.field textarea:focus{border-color:#91a3ff;box-shadow:0 0 0 3px rgba(61,98,255,.1)}.field textarea{min-height:102px;resize:vertical}.action-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:15px}.workflow{display:flex;align-items:center;gap:8px;overflow:auto;padding:7px 2px 16px}.stage{min-width:120px;border:1px solid var(--line);border-radius:13px;padding:12px;background:#fbfcff}.stage .stage-icon{font-size:18px}.stage strong{display:block;font-size:12px;margin-top:7px}.stage small{color:var(--muted);font-size:11px}.arrow{color:#a1acc2;font-size:18px}.stage.ready{border-color:#a4e0cb;background:#f0fbf7}.stage.running{border-color:#a5b5ff;background:#f3f5ff}.result-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.result{background:#f8fafc;border-radius:11px;padding:12px}.result span{display:block;color:var(--muted);font-size:11px}.result strong{display:block;font-size:16px;margin-top:3px}.result.pass{background:#effaf6;color:#167456}.result.fail{background:#fff2f3;color:#a63849}.table-wrap{overflow:auto}.table{width:100%;border-collapse:collapse;font-size:12px}.table th,.table td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);white-space:nowrap}.table th{color:var(--muted);font-weight:650}.pill{display:inline-flex;padding:4px 8px;border-radius:99px;background:#edf1f8;color:#5b6880;font-size:11px}.pill.green{background:#e8f8f1;color:#167456}.pill.amber{background:#fff5df;color:#9b6616}.empty{color:var(--muted);font-size:12px;padding:16px 0}.json{margin:0;background:#10182b;color:#dce6ff;border-radius:12px;padding:15px;max-height:280px;overflow:auto;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap}.toast{position:fixed;right:24px;bottom:24px;padding:12px 16px;border-radius:12px;background:#15213c;color:#fff;box-shadow:var(--shadow);opacity:0;transform:translateY(10px);pointer-events:none;transition:.2s}.toast.show{opacity:1;transform:translateY(0)}@media(max-width:1050px){.shell{grid-template-columns:76px 1fr}.sidebar{padding:20px 12px}.brand span,.nav button span,.side-note{display:none}.nav button{justify-content:center}.main{padding:22px}.hero,.layout{grid-template-columns:1fr}}@media(max-width:680px){.shell{display:block}.sidebar{display:none}.main{padding:18px}.topbar{align-items:flex-start}.topbar h1{font-size:23px}.hero-stats{grid-template-columns:repeat(4,1fr)}.stat{padding:11px 8px}.stat strong{font-size:17px}.templates{grid-template-columns:repeat(2,1fr)}.result-grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class='shell'>
  <aside class='sidebar'>
    <div class='brand'><div class='brand-mark'>✦</div><span>SimLoom AI</span></div>
    <nav class='nav'>
      <button class='active' onclick="showPanel('workspace', this)"><span>⌂</span><span>工程工作台</span></button>
      <button onclick="showPanel('projects', this)"><span>▣</span><span>项目历史</span></button>
      <button onclick="showPanel('assets', this)"><span>◇</span><span>工程资产</span></button>
      <button onclick="showPanel('settings', this)"><span>⚙</span><span>连接与配置</span></button>
    </nav>
    <div class='side-note'><strong>Offline first</strong>没有商业软件也能运行完整工程链路。接入 Windows bridge 后，可替换为真实软件执行。</div>
  </aside>
  <main class='main'>
    <header class='topbar'><div><div class='eyebrow'>Engineering workspace</div><h1>把工程目标变成可验证的流程</h1></div><div class='health'><i class='dot'></i><span id='healthText'>Runtime ready</span></div></header>
    <section class='hero'>
      <div class='hero-card'><div class='eyebrow' style='color:#b8c7ff'>CATIA · ANSYS · ADAMS · VI-GRADE</div><h2>从一句话开始，完成一条完整的工程链。</h2><p>规划模型、生成输入、运行流程、检查指标，并把每个结果保存为可追溯的工程资产。</p><div class='hero-actions'><button class='btn btn-primary' onclick="focusComposer()">开始一个工程任务 <span>→</span></button><button class='btn btn-ghost' onclick="runOffline()">运行离线演示</button></div></div>
      <div class='hero-stats'><div class='stat'><span>当前模式</span><strong id='modeValue'>Offline</strong></div><div class='stat'><span>工程阶段</span><strong id='stageValue'>4</strong></div><div class='stat'><span>验证状态</span><strong id='verdictValue'>待运行</strong></div><div class='stat'><span>任务数量</span><strong id='taskValue'>0</strong></div></div>
    </section>
    <section id='workspace' class='layout panel'>
      <div class='card'>
        <div class='card-head'><div><h3>创建工程任务</h3><p>选择一个模板，或直接描述你的工程目标。</p></div><span class='pill'>Step 01 · Define</span></div>
        <div class='templates'>
          <button class='template selected' onclick="selectTemplate(this,'悬架结构分析','用 CATIA 建立悬架模型，导入 Ansys 做静力分析，检查最大应力和位移。')"><div class='template-icon'>◈</div><strong>悬架结构</strong><span>CATIA → Ansys</span></button>
          <button class='template' onclick="selectTemplate(this,'悬架动力学','用 CATIA 建立悬架模型，导入 Adams 做多体动力学分析。')"><div class='template-icon'>⌁</div><strong>悬架动力学</strong><span>CATIA → Adams</span></button>
          <button class='template' onclick="selectTemplate(this,'整车操稳','用 Adams 和 VI-grade 做阶跃转向、操稳和圈速分析。')"><div class='template-icon'>◎</div><strong>整车操稳</strong><span>Adams → VI-grade</span></button>
          <button class='template' onclick="selectTemplate(this,'自定义流程','')"><div class='template-icon'>＋</div><strong>自定义</strong><span>从自然语言开始</span></button>
        </div>
        <div class='field'><label for='projectRoot'>项目目录</label><input id='projectRoot' value='./examples/offline_demo' placeholder='例如 ./projects/suspension_demo'></div>
        <div class='field'><label for='prompt'>工程目标</label><textarea id='prompt'>用 CATIA 建立悬架模型，导入 Adams 做动力学，再用 VI-grade 做操稳分析。</textarea></div>
        <div class='action-row'><button class='btn btn-blue' onclick='generatePlan()'>生成 AI 计划</button><button class='btn btn-light' onclick='enqueueTask()'>加入任务队列</button><button class='btn btn-light' onclick='runOffline()'>运行离线闭环</button></div>
      </div>
      <div class='card'>
        <div class='card-head'><div><h3>工程流程</h3><p id='planHint'>等待生成计划</p></div><span class='pill green' id='planStatus'>Ready</span></div>
        <div class='workflow' id='workflow'><div class='stage ready'><div class='stage-icon'>◇</div><strong>CATIA</strong><small>几何建模</small></div><div class='arrow'>→</div><div class='stage'><div class='stage-icon'>△</div><strong>Ansys</strong><small>结构分析</small></div><div class='arrow'>→</div><div class='stage'><div class='stage-icon'>⌁</div><strong>Adams</strong><small>多体动力学</small></div><div class='arrow'>→</div><div class='stage'><div class='stage-icon'>◎</div><strong>VI-grade</strong><small>车辆指标</small></div></div>
        <div class='card-head' style='margin-top:6px'><div><h3>最近验证</h3><p id='resultHint'>离线闭环会在这里显示工程结论</p></div></div>
        <div class='result-grid' id='results'><div class='result'><span>最大应力</span><strong>—</strong></div><div class='result'><span>最大位移</span><strong>—</strong></div><div class='result'><span>网格质量</span><strong>—</strong></div><div class='result'><span>转向不足梯度</span><strong>—</strong></div></div>
      </div>
      <div class='card'>
        <div class='card-head'><div><h3>任务时间线</h3><p>查看任务状态和最近事件。</p></div><button class='btn btn-light' onclick='refreshTasks()'>刷新</button></div>
        <div class='table-wrap' id='tasks'><div class='empty'>还没有任务。生成计划或加入任务队列开始。</div></div>
      </div>
      <div class='card'>
        <div class='card-head'><div><h3>运行输出</h3><p>计划、报告和错误会保留在这里。</p></div><button class='btn btn-light' onclick="document.querySelector('#output').textContent=''">清空</button></div>
        <pre class='json' id='output'>等待操作…</pre>
      </div>
    </section>
    <section id='projects' class='panel' hidden><div class='card'><div class='card-head'><div><h3>项目历史</h3><p>来自本地 SQLite 队列的项目。</p></div><button class='btn btn-light' onclick='refreshProjects()'>刷新</button></div><div id='projectsList' class='empty'>点击刷新查看项目。</div></div></section>
    <section id='assets' class='panel' hidden><div class='card'><div class='card-head'><div><h3>工程资产</h3><p>扫描当前项目目录中的模型、结果和报告。</p></div><button class='btn btn-light' onclick='refreshAssets()'>扫描</button></div><div id='assetsList' class='empty'>点击扫描查看资产。</div></div></section>
    <section id='settings' class='panel' hidden><div class='card'><div class='card-head'><div><h3>连接与配置</h3><p>查看当前 runtime 和商业软件后端状态。</p></div><button class='btn btn-light' onclick='refreshDoctor()'>重新检测</button></div><pre class='json' id='doctor'>点击重新检测。</pre></div></section>
  </main>
</div>
<div class='toast' id='toast'></div>
<script>
const $=s=>document.querySelector(s), out=$('#output');
const icons={catia:'◇',ansys:'△',adams:'⌁','vi-grade':'◎'};
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2600)}
function projectRoot(){return $('#projectRoot').value.trim()||'.'}
function focusComposer(){$('#prompt').focus();window.scrollTo({top:260,behavior:'smooth'})}
function selectTemplate(el,title,text){document.querySelectorAll('.template').forEach(x=>x.classList.remove('selected'));el.classList.add('selected');if(text)$('#prompt').value=text;toast(title+'模板已选中')}
function showPanel(id,button){document.querySelectorAll('.panel').forEach(x=>x.hidden=x.id!==id);document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));button.classList.add('active');if(id==='projects')refreshProjects();if(id==='assets')refreshAssets();if(id==='settings')refreshDoctor()}
async function request(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw new Error(data.error||data.message||'请求失败');return data}
function renderPlan(data){const steps=data.plan?.steps||[];$('#planHint').textContent=steps.length?steps.map(x=>x.tool+' · '+x.action).join(' → '):'计划为空';$('#planStatus').textContent=data.validation?.valid?'Validated':'Review';$('#planStatus').className='pill '+(data.validation?.valid?'green':'amber');if(steps.length){$('#workflow').innerHTML=steps.map((step,index)=>`<div class='stage ${index===0?'ready':''}'><div class='stage-icon'>${icons[step.tool]||'◆'}</div><strong>${step.tool}</strong><small>${step.action||'operation'}</small></div>${index<steps.length-1?'<div class="arrow">→</div>':''}`).join('')}out.textContent=JSON.stringify(data,null,2)}
async function generatePlan(){try{const data=await request('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:$('#prompt').value,project_dir:projectRoot()})});renderPlan(data);toast('AI 计划已生成')}catch(error){out.textContent=error.message;toast(error.message)}}
async function enqueueTask(){try{const data=await request('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:$('#prompt').value,project_dir:projectRoot()})});out.textContent=JSON.stringify(data,null,2);refreshTasks();toast('任务已加入队列')}catch(error){out.textContent=error.message;toast(error.message)}}
function renderVerdicts(data){const values={max_stress:['最大应力','MPa'],max_displacement:['最大位移','mm'],mesh_quality:['网格质量',''],understeer_gradient:['转向不足梯度','deg/g']};$('#verdictValue').textContent=(data.verdicts||[]).every(x=>x.passed)?'通过':'需检查';$('#modeValue').textContent=data.simulation_backend==='offline-surrogate'?'Offline':'Live';const cards=(data.verdicts||[]).map(v=>{const meta=values[v.name]||[v.name,v.unit||''];return `<div class='result ${v.passed?'pass':'fail'}'><span>${meta[0]}</span><strong>${v.value===null?'—':v.value+' '+meta[1]}</strong></div>`}).join('');if(cards)$('#results').innerHTML=cards;$('#resultHint').textContent=`${(data.verdicts||[]).filter(x=>x.passed).length}/${(data.verdicts||[]).length} 项规则通过 · ${data.simulation_backend||'runtime'}`;out.textContent=JSON.stringify(data,null,2)}
async function runOffline(){try{$('#planStatus').textContent='Running';$('#planStatus').className='pill amber';const data=await request('/api/offline-run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_dir:projectRoot(),model_spec:{name:'workspace_suspension'}})});renderVerdicts(data);$('#planStatus').textContent='Committed';$('#planStatus').className='pill green';toast('离线工程闭环已完成')}catch(error){$('#planStatus').textContent='Failed';$('#planStatus').className='pill amber';out.textContent=error.message;toast(error.message)}}
function refreshTasks(){request('/api/tasks').then(items=>{const list=Array.isArray(items)?items:[];$('#taskValue').textContent=list.length;$('#tasks').innerHTML=list.length?`<table class='table'><tr><th>状态</th><th>任务</th><th>创建时间</th></tr>${list.slice(0,8).map(x=>`<tr><td><span class='pill ${x.status==='completed'?'green':''}'>${x.status}</span></td><td>${x.prompt||''}</td><td>${x.created_at||''}</td></tr>`).join('')}</table>`:'<div class="empty">还没有任务。生成计划或加入任务队列开始。</div>'}).catch(()=>{})}
function refreshProjects(){request('/api/projects').then(items=>{$('#projectsList').innerHTML=items.length?`<table class='table'><tr><th>项目</th><th>ID</th><th>创建时间</th></tr>${items.map(x=>`<tr><td>${x.name}</td><td>${x.id}</td><td>${x.created_at||''}</td></tr>`).join('')}</table>`:'<div class="empty">还没有历史项目。</div>'}).catch(error=>$('#projectsList').textContent=error.message)}
function refreshAssets(){request('/api/artifacts?root='+encodeURIComponent(projectRoot())).then(data=>{$('#assetsList').innerHTML=data.length?`<table class='table'><tr><th>文件</th><th>类型</th><th>大小</th></tr>${data.slice(0,30).map(x=>`<tr><td>${x.path||x.name||''}</td><td>${x.kind||x.format||'artifact'}</td><td>${x.bytes||''}</td></tr>`).join('')}</table>`:'<div class="empty">当前目录没有已提交资产。</div>'}).catch(error=>$('#assetsList').textContent=error.message)}
function refreshDoctor(){request('/api/doctor').then(data=>$('#doctor').textContent=JSON.stringify(data,null,2)).catch(error=>$('#doctor').textContent=error.message)}
request('/api/health').then(data=>{$('#healthText').textContent=data.ok?'Runtime ready':'Runtime unavailable'}).catch(()=>{$('#healthText').textContent='Runtime unavailable'});refreshTasks();
</script>
</body>
</html>"""


def create_server(agent: EngineeringAgent, queue: TaskQueue, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    """Build the local Web/API server without starting its blocking loop.

    Keeping construction separate makes the product shell embeddable in a
    desktop launcher and lets integration tests exercise the same HTTP
    surface on an ephemeral port.
    """
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
            if parsed.path == "/api/templates":
                return self._send(200, {
                    "templates": [
                        {"id": "suspension-structure", "name": "悬架结构", "tools": ["catia", "ansys"]},
                        {"id": "suspension-dynamics", "name": "悬架动力学", "tools": ["catia", "adams"]},
                        {"id": "vehicle-handling", "name": "整车操稳", "tools": ["adams", "vi-grade"]},
                        {"id": "custom", "name": "自定义流程", "tools": []},
                    ],
                    "schemaVersion": "simloom-ai/web/v1",
                })
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
            if self.path == "/api/offline-run":
                result = agent.run_offline_vehicle_chain(
                    body.get("project_dir", "."),
                    body.get("model_spec") or {},
                    body.get("rules"),
                )
                return self._send(200, result)
            if self.path == "/api/projects":
                project_id = queue.create_project(body.get("name", "unnamed-project"))
                return self._send(201, {"id": project_id, "name": body.get("name", "unnamed-project")})
            return self._send(404, {"error": "not found"})

        def log_message(self, *_args):
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(agent: EngineeringAgent, queue: TaskQueue, host: str = "127.0.0.1", port: int = 8787):
    server = create_server(agent, queue, host, port)
    print(f"Engineering Agent web UI: http://{host}:{port}")
    server.serve_forever()

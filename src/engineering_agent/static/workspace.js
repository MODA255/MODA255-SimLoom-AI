'use strict';
const $ = s => document.querySelector(s);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {project:null, templates:[], busy:false, page:1, preview:0};
const icons = {catia:'◇', ansys:'△', adams:'⌁', 'vi-grade':'◎'};
const labels = {queued:'待运行', running:'运行中', completed:'已完成', failed:'失败'};
let toastTimer;
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').classList.add('show'); toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),4000); }
function details(data) { $('#output').textContent=JSON.stringify(data,null,2); }
function reportError(error) { $('#runError').textContent=error.message; toast(error.message); }
async function request(url, body) {
  const response=await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  let data;
  try { data=await response.json(); } catch (_) { throw new Error('服务返回异常，请检查应用是否仍在运行。'); }
  if(!response.ok) throw new Error(data.error || '操作失败，请重试。');
  return data;
}
function busy(value) {
  state.busy=value;
  document.querySelectorAll('[data-action], .project-open, #planButton, #offlineButton, #queueButton, .run-task, #createProject, #retryTask').forEach(b=>b.disabled=value);
  if(value) $('#runError').textContent='';
}
function choosePanel(id) {
  $('.hero').hidden=id==='taskDetail';
  document.querySelectorAll('.panel').forEach(panel=>panel.hidden=panel.id!==id);
  document.querySelectorAll('[data-panel]').forEach(button=>button.classList.toggle('active',button.dataset.panel===id));
  if(id==='projects') refreshProjects().catch(reportError);
  if(id==='assets') refreshAssets().catch(reportError);
  if(id==='settings') refreshDoctor().catch(reportError);
}
function wizardPage(page) {
  state.page=page;
  $('#wizardPage1').hidden=page!==1; $('#wizardPage2').hidden=page!==2;
  $('#backWizard').hidden=page===1; $('#nextWizard').hidden=page===2; $('#createProject').hidden=page===1;
  $('#wizardStep').textContent=page===1?'01 / 02 · 项目':'02 / 02 · 工程参数';
  (page===1?$('#projectName'):$('#wheelbase')).focus();
}
function openWizard() {
  $('#wizardError').textContent=''; $('#projectForm').reset();
  $('#projectWizard').showModal(); wizardPage(1);
}
function projectRequired() { if(state.project) return true; openWizard(); return false; }
async function openProject(id) {
  const project=await request('/api/projects/'+encodeURIComponent(id));
  if(!project.project_dir) throw new Error('这是未关联目录的旧项目，请使用向导创建一个独立项目。');
  state.project=project; state.taskId=null; state.preview++;
  try { localStorage.setItem('simloom.project',id); } catch (_) {}
  $('#currentProject').textContent=project.name;
  $('#projectHint').textContent='当前项目：'+project.name;
  $('#prompt').value=project.metadata.prompt||'';
  $('#stageValue').textContent='—'; $('#verdictValue').textContent='待运行';
  $('#planStatus').textContent='待规划'; $('#planStatus').className='pill';
  $('#workflow').innerHTML='<div class="empty">生成计划后显示执行顺序。</div>';
  $('#results').innerHTML='<div class="empty">还没有运行结果</div>';
  $('#resultHint').textContent='运行后显示数值、阈值与判定。'; $('#viewResults').hidden=true;
  $('#preview').replaceChildren(); $('#assetDetails').textContent='—';
  $('#previewName').textContent='选择一个工程文件'; $('#downloadAsset').hidden=true;
  $('#previewHint').textContent='STEP / OBJ 模型 · CSV 曲线 · JSON 指标';
  choosePanel('workspace');
  await refreshTasks(true);
}
async function createProject(event) {
  event.preventDefault();
  if(state.page===1) { if($('#projectName').reportValidity()) wizardPage(2); return; }
  if(!$('#projectForm').reportValidity()) return;
  const body={name:$('#projectName').value, template:$('#projectTemplate').value,
    parameters:{wheelbase_mm:Number($('#wheelbase').value),track_front_mm:Number($('#track').value),chassis_height_mm:Number($('#height').value),force_n:Number($('#force').value)}};
  if($('#projectDirectory').value.trim()) body.project_dir=$('#projectDirectory').value.trim();
  $('#createProject').disabled=true;
  try { const project=await request('/api/projects',body); $('#projectWizard').close(); await openProject(project.id); toast('项目已创建，参数已保存'); }
  catch(error) { $('#wizardError').textContent=error.message; }
  finally { $('#createProject').disabled=false; }
}
async function refreshProjects() {
  const projects=await request('/api/projects');
  $('#projectsList').innerHTML=projects.length?projects.map(p=>`<article class="project-card"><h3>${escapeHTML(p.name)}</h3><span class="pill">${escapeHTML(state.templates.find(t=>t.id===p.metadata.template)?.name||'历史项目')}</span><p class="muted">${escapeHTML(p.project_dir||'未关联目录')}</p><p class="muted">${escapeHTML(new Date(p.created_at).toLocaleString())}</p><button class="btn btn-blue project-open" data-project="${escapeHTML(p.id)}" ${state.busy?'disabled':''}>打开项目 →</button></article>`).join(''):'<div class="empty">还没有项目，点击「新建项目」开始。</div>';
}
function renderPlan(data) {
  const steps=data.plan?.steps||[]; $('#stageValue').textContent=steps.length;
  $('#planHint').textContent='这是执行计划；真实软件尚未运行。';
  $('#planStatus').textContent=data.validation?.valid?'计划有效':'需检查';
  $('#planStatus').className='pill '+(data.validation?.valid?'green':'amber');
  $('#workflow').innerHTML=steps.map((s,i)=>`<div class="stage"><div class="stage-icon">${icons[s.tool]||'◇'}</div><strong>${escapeHTML(s.tool)}</strong><small>${escapeHTML(s.action)}</small></div>${i<steps.length-1?'<span class="arrow">→</span>':''}`).join('')||'<div class="empty">没有可执行阶段，请补充目标。</div>';
  details(data);
}
async function plan() {
  if(!projectRequired()||state.busy) return;
  busy(true);
  try { renderPlan(await request('/api/plan',{project_id:state.project.id,prompt:$('#prompt').value})); }
  catch(error) { reportError(error); } finally { busy(false); }
}
async function enqueue() {
  if(!projectRequired()||state.busy) return;
  busy(true);
  try { await request('/api/tasks',{project_id:state.project.id,prompt:$('#prompt').value}); await refreshTasks(); toast('排练任务已保存，点击「运行排练」即可执行。'); }
  catch(error) { reportError(error); } finally { busy(false); }
}
function renderVerdicts(result) {
  const titles={max_stress:'最大应力',max_displacement:'最大位移',mesh_quality:'网格质量',understeer_gradient:'转向不足梯度'};
  const verdicts=result.verdicts||[];
  $('#verdictValue').textContent=verdicts.length?(verdicts.every(v=>v.passed)?'全部通过':'需检查'):'无规则';
  $('#results').innerHTML=verdicts.map(v=>`<div class="result ${v.passed?'pass':'fail'}"><span>${escapeHTML(titles[v.name]||v.name)}</span><strong>${v.value==null?'缺失':escapeHTML(Number(v.value).toLocaleString(undefined,{maximumFractionDigits:4}))} ${escapeHTML(v.unit)}</strong><small>${escapeHTML(v.operator)} ${escapeHTML(v.target)} · ${v.passed?'通过':'未通过'}</small></div>`).join('');
  $('#resultHint').textContent=`离线示例数据 · ${verdicts.filter(v=>v.passed).length}/${verdicts.length} 项规则通过`;
  $('#workflow').innerHTML=(result.stages||[]).map((s,i)=>`<div class="stage ready"><div class="stage-icon">${icons[s.tool]||'◇'}</div><strong>${escapeHTML(s.tool)}</strong><small>离线产物已生成</small></div>${i<(result.stages.length-1)?'<span class="arrow">→</span>':''}`).join('');
  $('#stageValue').textContent=(result.stages||[]).length;
  $('#planHint').textContent='四阶段离线演示已完成，点击下方查看模型与结果。';
  $('#planStatus').textContent='已生成产物'; $('#planStatus').className='pill green';
  $('#viewResults').hidden=false; details(result);
}
async function runOffline(demo=false) {
  if(state.busy) return;
  if(!state.project&&!demo) { openWizard(); return; }
  busy(true);
  try {
    if(!state.project) { const p=await request('/api/projects',{name:'四软件离线体验',template:'offline-vehicle'}); await openProject(p.id); }
    $('#planStatus').textContent='运行中'; $('#planStatus').className='pill amber';
    $('#resultHint').textContent='正在生成模型和离线结果，请稍候…';
    const result=await request('/api/offline-run',{project_id:state.project.id});
    renderVerdicts(result); await refreshTasks(); await showTask(result.task_id); toast('报告已生成，可以查看阶段、产物和工程指标。');
  } catch(error) { $('#planStatus').textContent='运行失败'; $('#resultHint').textContent='本次运行未完成，请查看任务错误。'; reportError(error); await refreshTasks(); }
  finally { busy(false); }
}
async function refreshTasks(restore=false) {
  const id=state.project?.id;
  if(!id) { $('#tasks').innerHTML='<div class="empty">创建或打开项目后查看任务。</div>'; return; }
  const tasks=await request('/api/tasks?project_id='+encodeURIComponent(id));
  if(state.project?.id!==id) return;
  $('#taskValue').textContent=tasks.length;
  $('#tasks').innerHTML=tasks.length?`<table class="table"><thead><tr><th>状态</th><th>任务</th><th>时间</th><th>操作</th></tr></thead><tbody>${tasks.map(t=>`<tr><td><span class="pill ${t.status==='completed'?'green':''}">${escapeHTML(labels[t.status]||t.status)}</span></td><td>${escapeHTML(t.prompt)}${t.result?.error?`<p class="error">${escapeHTML(t.result.error)}</p>`:''}</td><td>${escapeHTML(new Date(t.created_at).toLocaleString())}</td><td>${t.status==='queued'?`<button class="btn btn-light run-task" data-run="${escapeHTML(t.id)}" ${state.busy?'disabled':''}>${t.mode==='offline'?'运行离线任务':'运行排练'}</button> `:''}<button class="btn btn-light" data-task="${escapeHTML(t.id)}">查看详情</button></td></tr>`).join('')}</tbody></table>`:'<div class="empty">还没有任务。生成计划或运行离线闭环开始。</div>';
  if(restore) { const last=tasks.find(t=>t.result?.verdicts); if(last) renderVerdicts(last.result); }
}
async function runTask(id) {
  if(state.busy) return; busy(true);
  try { const data=await request('/api/tasks/'+encodeURIComponent(id)+'/run',{}); details(data); await refreshTasks(); await showTask(id); toast('任务执行结束，查看详情了解结果。'); }
  catch(error) { reportError(error); await refreshTasks(); await showTask(id); } finally { busy(false); }
}
async function refreshAssets() {
  const project=state.project;
  if(!project) { $('#assetsList').innerHTML='<div class="empty">请先创建或打开项目。</div>'; return; }
  $('#assetProject').textContent=project.name;
  const items=await request('/api/artifacts?root='+encodeURIComponent(project.project_dir));
  if(state.project?.id!==project.id) return;
  $('#assetsList').innerHTML=items.length?items.map(item=>`<button class="asset-button" data-asset="${escapeHTML(item.path)}">${escapeHTML(item.path)}<small>${escapeHTML(item.suffix||'文件')} · ${(item.bytes/1024).toFixed(1)} KB</small></button>`).join(''):'<div class="empty">暂无产物，请先运行离线闭环。</div>';
}
async function previewAsset(path) {
  const version=++state.preview;
  const query=new URLSearchParams({root:state.project.project_dir,path});
  $('#previewName').textContent=path.split('/').pop(); $('#previewHint').textContent='正在读取…';
  $('#preview').innerHTML='<div class="empty">正在准备预览…</div>';
  $('#downloadAsset').href='/api/download?'+query; $('#downloadAsset').hidden=false;
  document.querySelectorAll('[data-asset]').forEach(b=>b.classList.toggle('active',b.dataset.asset===path));
  try {
    const suffix=path.split('.').pop().toLowerCase();
    if(!['step','stp','obj','dxf','csv','json'].includes(suffix)) {
      const data=await request('/api/artifact?'+query); if(version!==state.preview) return;
      const pre=document.createElement('pre'); pre.className='json'; pre.textContent=typeof data.content==='string'?data.content:JSON.stringify(data.content,null,2);
      $('#preview').replaceChildren(pre); $('#assetDetails').textContent=JSON.stringify(data,null,2); $('#previewHint').textContent='文本预览；可下载原始文件。'; return;
    }
    const data=await request('/api/asset-view?'+query); if(version!==state.preview) return;
    const frame=document.createElement('iframe'); frame.className='preview-frame'; frame.title='工程资产预览'; frame.setAttribute('sandbox','allow-scripts');
    const interactive=data.viewer.includes('engineering-3d-viewer');
    frame.srcdoc='<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\';"><style>body{margin:0;font:14px system-ui;background:#f7f9fc}svg{width:100%;height:430px}button{cursor:pointer}</style>'+(interactive?data.viewer:data.svg);
    $('#preview').replaceChildren(frame); $('#assetDetails').textContent=JSON.stringify(data.asset,null,2);
    $('#previewHint').textContent=data.preview_note||(interactive?'拖动旋转 · 滚轮缩放 · 双击重置':'文件数据预览');
  } catch(error) { if(version!==state.preview) return; $('#preview').textContent=error.message; $('#previewHint').textContent='预览失败，仍可下载原始文件。'; }
}
async function refreshDoctor() {
  $('#doctor').textContent='正在检测本地环境…';
  const [capabilities,items]=await Promise.all([request('/api/capabilities'),request('/api/doctor')]);
  $('#capabilities').innerHTML=(capabilities.modes||[]).map(mode=>`<div class="status-card mode-card"><strong>${escapeHTML(mode.label)}</strong><p class="muted">${escapeHTML(mode.reason)}</p><span class="pill ${mode.ready?'green':'amber'}">${mode.ready?'可用':'未就绪'}</span></div>`).join('');
  $('#doctor').innerHTML=items.map(item=>`<div class="status-card"><strong>${escapeHTML(item.tool||item.name||'环境')}</strong><p class="muted">${escapeHTML(item.message||item.reason||item.status||'查看详情')}</p><span class="pill ${item.available?'green':'amber'}">${item.available?'可用':'未就绪 / 需配置'}</span><details><summary>诊断详情</summary><pre class="json">${escapeHTML(JSON.stringify(item,null,2))}</pre></details></div>`).join('');
}
document.addEventListener('click',event=>{
  const target=event.target.closest('button'); if(!target||target.disabled) return;
  if(target.dataset.panel) choosePanel(target.dataset.panel);
  if(target.dataset.action==='new-project') openWizard();
  if(target.dataset.action==='demo') runOffline(true);
  if(target.dataset.project) openProject(target.dataset.project).catch(reportError);
  if(target.dataset.asset) previewAsset(target.dataset.asset).catch(reportError);
  if(target.dataset.run) runTask(target.dataset.run).catch(reportError);
  if(target.dataset.task) showTask(target.dataset.task).catch(reportError);
  if(target.dataset.template) { const t=state.templates.find(x=>x.id===target.dataset.template); $('#prompt').value=t.prompt; document.querySelectorAll('[data-template]').forEach(b=>b.classList.toggle('selected',b===target)); }
});
$('#projectForm').addEventListener('submit',createProject);
$('#nextWizard').onclick=()=>{if($('#projectName').reportValidity()) wizardPage(2);};
$('#backWizard').onclick=()=>wizardPage(1); $('#cancelWizard').onclick=()=>$('#projectWizard').close();
$('#planButton').onclick=plan; $('#queueButton').onclick=enqueue; $('#offlineButton').onclick=()=>runOffline();
$('#refreshTasks').onclick=()=>refreshTasks().catch(reportError); $('#refreshAssets').onclick=()=>refreshAssets().catch(reportError);
$('#refreshDoctor').onclick=()=>refreshDoctor().catch(reportError); $('#viewResults').onclick=()=>choosePanel('assets');
async function init() {
  try {
    const [health,templates,workspace]=await Promise.all([request('/api/health'),request('/api/templates'),request('/api/workspace')]);
    $('#healthText').textContent=health.ok?'● 本地服务已连接':'服务未连接';
    state.templates=templates.templates;
    $('#projectTemplate').innerHTML=state.templates.map(t=>`<option value="${escapeHTML(t.id)}">${escapeHTML(t.name)}</option>`).join('');
    $('#templates').innerHTML=state.templates.filter(t=>t.id!=='offline-vehicle').map(t=>`<button class="template" data-template="${escapeHTML(t.id)}"><span class="template-icon">◇</span><strong>${escapeHTML(t.name)}</strong><span>${escapeHTML(t.tools.join(' → ')||'自由描述')}</span></button>`).join('');
    $('#defaultRoot').textContent='默认保存到：'+workspace.projects_root;
    let saved; try { saved=localStorage.getItem('simloom.project'); } catch (_) {}
    if(saved) { try { await openProject(saved); } catch (_) { toast('上次项目未找到，请从历史中选择或新建。'); } }
    await refreshTasks();
  } catch(error) { $('#healthText').textContent='服务连接失败'; reportError(error); }
}
setInterval(()=>{if(state.busy&&state.project) refreshTasks().catch(()=>{});},2000);
init();

const metricTitles={max_stress:'最大应力',max_displacement:'最大位移',mesh_quality:'网格质量',understeer_gradient:'转向不足梯度'};
const taskStatusLabels = {...labels, planned:'排练通过', blocked:'依赖阻塞', unavailable:'不可用', rolled_back:'已回滚'};
const displayTime = value => value ? new Date(value).toLocaleString() : '未记录';
const displayDuration = value => value == null ? '未记录' : `${Number(value).toFixed(3)} 秒`;
function reportTable(headers, rows) {
  return `<div class="table-wrap"><table class="table"><thead><tr>${headers.map(h=>`<th>${escapeHTML(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(c=>`<td>${escapeHTML(c ?? '未记录')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function renderTaskReport(report) {
  $('#taskTitle').textContent=report.prompt;
  $('#taskSubtitle').textContent=`${report.project_name} · ${report.task_id}`;
  $('#taskMode').textContent=report.mode_label;
  $('#taskStatus').textContent=taskStatusLabels[report.status]||report.status;
  $('#taskStatus').className='pill '+(report.status==='completed'?'green':'amber');
  $('#taskNotice').textContent=report.notice;
  $('#taskDuration').textContent=displayDuration(report.duration_seconds);
  $('#taskStarted').textContent=displayTime(report.started_at);
  $('#taskFinished').textContent=displayTime(report.finished_at);
  $('#taskConclusion').textContent=({passed:'全部规则通过',failed:'存在未通过的规则',not_evaluated:'尚未判定'})[report.validation_status];
  $('#taskFailure').hidden=!report.error;
  $('#taskFailureText').textContent=report.error||'';
  $('#taskNextStep').textContent=report.next_step;
  $('#retryTask').hidden=!report.retry_allowed;
  $('#retryTask').disabled=state.busy;
  $('#taskRetryOrigin').hidden=!report.retry_of;
  $('#taskRetryOrigin').textContent=report.retry_of ? '查看原失败任务' : '';
  $('#taskRetryOrigin').dataset.task=report.retry_of||'';
  $('#taskStages').innerHTML=report.stages.length?reportTable(['阶段','软件','状态','耗时','说明'],report.stages.map(s=>[s.label,s.tool||'工作台',taskStatusLabels[s.status]||s.status,displayDuration(s.duration_seconds),s.error||s.message||''])):'<p class="muted">开始运行后显示阶段记录。旧任务可能没有耗时信息。</p>';
  $('#taskMetrics').innerHTML=report.verdicts.length?reportTable(['指标','数值','单位','规则','判定'],report.verdicts.map(v=>[metricTitles[v.name]||v.name,v.value,v.unit,`${v.operator} ${v.target}`,v.passed?'通过':'未通过'])):'<p class="muted">本次任务尚无指标判定；排练不会生成仿真结论。</p>';
  const input=report.input?.model_spec;
  const values={...(input?.parameters||{}),...(input?.load_cases?.[0]||{})};
  const parameterNames={wheelbase_mm:'长度 / 轴距（mm）',track_front_mm:'宽度 / 轮距（mm）',chassis_height_mm:'几何高度（mm）',force_n:'载荷（N）'};
  $('#taskInput').innerHTML=input?reportTable(['工程参数','本次使用值'],Object.entries(parameterNames).map(([key,name])=>[name,values[key]])):'<p class="muted">此旧任务未保存输入快照，不使用当前参数代替历史输入。</p>';
  $('#taskInputJson').textContent=JSON.stringify(report.input,null,2);
  $('#taskPlan').innerHTML=report.plan?.steps?.length?reportTable(['步骤','软件','动作','依赖'],report.plan.steps.map(s=>[s.id,s.tool,s.action==='offline_surrogate'?'离线示例':s.action,(s.depends_on||[]).join(' → ')||'无'])):'<p class="muted">暂无执行计划记录。</p>';
  $('#taskArtifacts').innerHTML=report.artifacts.length?report.artifacts.map(a=>{
    const query=new URLSearchParams({root:report.project_dir,path:a.path});
    return `<article class="task-artifact"><div><strong>${escapeHTML(a.name)}</strong><p class="muted">${escapeHTML(a.path)} · ${Number(a.bytes).toLocaleString()} 字节</p><code>SHA-256 ${escapeHTML(a.sha256)}</code></div><a class="btn btn-light btn-link" href="/api/download?${escapeHTML(query.toString())}" download>下载</a></article>`;
  }).join(''):'<p class="muted">尚无已保存的历史产物；旧任务的文件哈希不会补造。</p>';
  const eventNames={queued:'任务已创建',running:'开始执行',plan:'计划已记录',stage_started:'阶段开始',stage_finished:'阶段结束',completed:'任务已完成',failed:'任务失败',execution_mode:'运行模式已记录'};
  $('#taskEvents').innerHTML=report.events.map(e=>`<li><time>${escapeHTML(displayTime(e.created_at))}</time><div><strong>${escapeHTML(eventNames[e.event]||e.event)}</strong><p>${escapeHTML(e.payload.label||e.payload.error||'')}${e.payload.duration_seconds==null?'':` · ${escapeHTML(displayDuration(e.payload.duration_seconds))}`}</p></div></li>`).join('');
  $('#taskEvidence').textContent=JSON.stringify(report,null,2);
  for(const format of ['html','json']) $('#report'+format).href=`/api/tasks/${encodeURIComponent(report.task_id)}/report?format=${format}&download=1`;
}
async function showTask(id, refresh=false) {
  state.taskId=id;
  if(!refresh) { choosePanel('taskDetail'); $('#taskDetailContent').hidden=true; $('#taskLoading').hidden=false; $('#taskLoading').textContent='正在读取任务记录…'; }
  const report=await request('/api/tasks/'+encodeURIComponent(id)+'/report');
  if(state.taskId!==id) return;
  renderTaskReport(report); state.taskStatus=report.status;
  $('#taskDetailContent').hidden=false; $('#taskLoading').hidden=true;
}
async function retryCurrentTask() {
  if(state.busy||!state.taskId) return;
  const original=state.taskId; let child;
  busy(true); $('#retryTask').disabled=true;
  try {
    child=await request('/api/tasks/'+encodeURIComponent(original)+'/retry',{});
    await showTask(child.id);
    const result=await request('/api/tasks/'+encodeURIComponent(child.id)+'/run',{});
    if(result.verdicts) renderVerdicts(result);
    toast('重试已完成，原失败记录已保留。');
  } catch(error) { reportError(error); }
  finally {
    busy(false);
    await refreshTasks();
    await showTask(child?.id||original,true);
  }
}
$('#retryTask').onclick=()=>retryCurrentTask().catch(reportError);
$('#refreshTaskDetail').onclick=()=>{if(state.taskId) showTask(state.taskId,true).catch(reportError);};
let taskRefreshing=false;
setInterval(async()=>{
  if(taskRefreshing||!state.taskId||$('#taskDetail').hidden||!['queued','running'].includes(state.taskStatus)) return;
  taskRefreshing=true;
  try { await showTask(state.taskId,true); } catch(_) {} finally { taskRefreshing=false; }
},1500);

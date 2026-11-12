'use strict';
const $ = s => document.querySelector(s);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {project:null, templates:[], busy:false, page:1, preview:0, previewPath:null, versions:[], versionView:0};
const icons = {catia:'◇', ansys:'△', adams:'⌁', 'vi-grade':'◎'};
const labels = {waiting:'等待恢复连接',queued:'待运行', running:'运行中', completed:'已完成', failed:'失败'};
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
  document.querySelectorAll('[data-action], .project-open, #planButton, #offlineButton, #queueButton, .run-task, #createProject, #retryTask, #resumeTask, #drawingTools button, #drawingTools input, #drawingTools select').forEach(b=>b.disabled=value);
  if(value) $('#runError').textContent='';
}
function choosePanel(id) {
  $('.hero').hidden=id==='taskDetail';
  document.querySelectorAll('.panel').forEach(panel=>panel.hidden=panel.id!==id);
  document.querySelectorAll('[data-panel]').forEach(button=>button.classList.toggle('active',button.dataset.panel===id));
  if(id==='projects') { refreshProjects().catch(reportError); refreshVersions().catch(reportError); }
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
  state.project=project; state.taskId=null; state.preview++; state.previewPath=null;
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
  $('#drawingTools').hidden=true; state.drawing=null; state.drawingPath=null;
  $('#previewName').textContent='选择一个工程文件'; $('#downloadAsset').hidden=true;
  $('#previewHint').textContent='STEP / OBJ 模型 · CSV 曲线 · JSON 指标';
  resetVersionPanel();
  choosePanel('workspace');
  await refreshTasks(true);
  await refreshVersions();
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
function resetVersionPanel() {
  state.versions=[]; state.versionView++;
  $('#versionTimeline').innerHTML='<div class="empty">打开项目后查看版本时间线。</div>';
  $('#versionList').replaceChildren();
  $('#versionLabel').value=''; $('#createVersion').disabled=true;
  $('#versionHistoryHint').textContent='打开项目后查看快照、分支关系和 DXF 几何变化。';
  for(const id of ['versionLeft','versionRight']) { const select=$('#'+id); select.innerHTML='<option value="">请选择版本</option>'; select.disabled=true; }
  $('#versionDiffButton').disabled=true;
  $('#versionDiffStatus').textContent=''; $('#versionDiffSummary').hidden=true; $('#versionDiffSummary').replaceChildren();
  $('#versionDiffPreview').hidden=true; $('#versionDiffPreview').replaceChildren(); $('#versionDiffDetails').hidden=true; $('#versionDiffJson').textContent='';
  $('#versionInspect').hidden=true; $('#versionInspectPreview').replaceChildren(); $('#versionInspectAssets').replaceChildren();
}
async function createVersionSnapshot() {
  if(!state.project) { $('#versionDiffStatus').textContent='请先打开项目。'; return; }
  const label=$('#versionLabel').value.trim();
  if(!label) { $('#versionDiffStatus').textContent='请先填写版本名称。'; $('#versionLabel').focus(); return; }
  const button=$('#createVersion'); button.disabled=true; $('#versionDiffStatus').textContent='正在收集项目资产并保存快照…';
  try {
    const ordered=[...state.versions].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
    const result=await request('/api/version',{project_id:state.project.id,label,parent:ordered[0]?.id});
    $('#versionLabel').value=''; details(result); toast('项目版本已保存。'); await refreshVersions();
    $('#versionDiffStatus').textContent=`已保存版本：${result.label||label}。`;
  } catch(error) { $('#versionDiffStatus').textContent='保存失败：'+error.message; toast(error.message); }
  finally { button.disabled=!state.project; }
}
function versionLabel(version) {
  const stamp=version.created_at ? new Date(version.created_at).toLocaleString() : '未记录时间';
  return `${version.label||'未命名版本'} · ${stamp}${version.has_dxf?' · DXF':''}`;
}
function renderVersionSelects() {
  const options=state.versions.map(version=>`<option value="${escapeHTML(version.id)}">${escapeHTML(versionLabel(version))}</option>`).join('');
  for(const id of ['versionLeft','versionRight']) { const select=$('#'+id); select.innerHTML='<option value="">请选择版本</option>'+options; select.disabled=state.versions.length<2; }
  const dxfVersions=state.versions.filter(version=>version.has_dxf);
  if(dxfVersions.length>=2) {
    const ordered=[...dxfVersions].sort((a,b)=>String(a.created_at||'').localeCompare(String(b.created_at||'')));
    $('#versionLeft').value=ordered[ordered.length-2].id;
    $('#versionRight').value=ordered[ordered.length-1].id;
  }
  $('#versionDiffButton').disabled=dxfVersions.length<2;
}
function renderVersionList() {
  const versions=[...state.versions].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
  $('#versionList').innerHTML=versions.length?versions.map(version=>`<article class="version-card ${version.has_dxf?'has-dxf':''}"><div><strong>${escapeHTML(version.label||'未命名版本')}</strong><p class="muted">${escapeHTML(version.created_at?new Date(version.created_at).toLocaleString():'未记录时间')} · ${version.object_count||0} 个资产${version.has_dxf?' · 包含 DXF':''}</p>${version.parent?`<p class="muted">父版本：${escapeHTML(version.parent)}</p>`:''}</div><button class="btn btn-light" data-version-view="${escapeHTML(version.id)}">查看快照</button></article>`).join(''):'<div class="empty">本项目还没有版本快照。</div>';
}
function renderVersionTimeline(data) {
  const frame=document.createElement('iframe'); frame.className='timeline-frame'; frame.title='项目版本时间线'; frame.setAttribute('sandbox','');
  frame.srcdoc=`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#f7f9fc}svg{display:block;width:100%;height:auto;min-height:160px}</style>${data.svg||''}`;
  $('#versionTimeline').replaceChildren(frame);
}
async function refreshVersions() {
  const project=state.project;
  if(!project) { resetVersionPanel(); return; }
  $('#createVersion').disabled=false;
  const view=++state.versionView;
  $('#refreshVersions').disabled=true; $('#versionHistoryHint').textContent='正在读取项目版本…';
  try {
    const data=await request('/api/versions?project_id='+encodeURIComponent(project.id));
    if(view!==state.versionView||state.project?.id!==project.id) return;
    state.versions=data.versions||[];
    renderVersionSelects();
    renderVersionList();
    if(data.svg&&data.events?.length) renderVersionTimeline(data); else $('#versionTimeline').innerHTML='<div class="empty">本项目还没有版本快照。完成一次 CAD 操作或创建版本后，这里会出现时间线。</div>';
    const dxfCount=state.versions.filter(version=>version.has_dxf).length;
    $('#versionHistoryHint').textContent=state.versions.length?`${state.versions.length} 个版本快照 · ${dxfCount} 个版本包含 DXF 几何。`:'本项目还没有版本快照。';
    $('#versionDiffStatus').textContent=dxfCount<2?'至少需要两个包含 DXF 几何的版本才能生成视觉 diff。':'';
  } catch(error) {
    if(view===state.versionView) { $('#versionHistoryHint').textContent=error.message; $('#versionTimeline').innerHTML='<div class="empty">版本读取失败。</div>'; }
  } finally { if(view===state.versionView) $('#refreshVersions').disabled=false; }
}
async function inspectVersion(versionId, assetIndex=0) {
  if(!state.project||!versionId) return;
  const project=state.project;
  $('#versionInspect').hidden=false; $('#versionInspectTitle').textContent='正在读取历史版本…'; $('#versionInspectMeta').textContent='';
  $('#versionInspectPreview').innerHTML='<div class="empty">正在准备快照预览…</div>'; $('#versionInspectAssets').replaceChildren();
  try {
    const query=new URLSearchParams({project_id:project.id,version_id:versionId,asset_index:String(assetIndex)});
    const data=await request('/api/version-view?'+query);
    if(state.project?.id!==project.id) return;
    $('#versionInspectTitle').textContent=data.version.label||'历史版本预览';
    $('#versionInspectMeta').textContent=`${data.version.created_at?new Date(data.version.created_at).toLocaleString():'未记录时间'} · ${data.asset.name||'未命名资产'}`;
    const frame=document.createElement('iframe'); frame.className='version-inspect-frame'; frame.title='历史版本资产预览'; frame.setAttribute('sandbox','');
    frame.srcdoc=`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#f8fafc}svg{display:block;width:100%;height:auto;min-height:260px}</style>${data.svg||''}`;
    $('#versionInspectPreview').replaceChildren(frame);
    $('#versionInspectAssets').innerHTML=(data.assets||[]).map(item=>`<button class="version-asset ${item.index===data.asset.index?'active':''}" data-version-id="${escapeHTML(data.version.id)}" data-version-asset="${item.index}"><strong>${escapeHTML(item.name||'未命名资产')}</strong><small>${escapeHTML(item.format||item.kind||'资产')} · ${escapeHTML(item.path||'快照内对象')}</small></button>`).join('');
  } catch(error) { $('#versionInspectTitle').textContent='历史版本读取失败'; $('#versionInspectMeta').textContent=error.message; $('#versionInspectPreview').innerHTML='<div class="empty">无法预览此版本。</div>'; }
}
function renderVersionDiff(result) {
  const visual=result.visual_diff||{}; const counts=visual.counts||{};
  const labels={added:'新增',removed:'删除',changed:'修改',unchanged:'未变化'};
  $('#versionDiffSummary').innerHTML=Object.keys(labels).map(key=>`<div class="version-count ${key}"><span>${labels[key]}</span><strong>${Number(counts[key]||0).toLocaleString()}</strong></div>`).join('');
  $('#versionDiffSummary').hidden=false;
  const frame=document.createElement('iframe'); frame.className='diff-frame'; frame.title='版本视觉差异'; frame.setAttribute('sandbox','');
  frame.srcdoc=`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#f8fafc}svg{display:block;width:100%;height:auto;min-height:240px}</style>${visual.svg||''}`;
  $('#versionDiffPreview').replaceChildren(frame); $('#versionDiffPreview').hidden=false;
  $('#versionDiffJson').textContent=JSON.stringify(result.diff||{},null,2); $('#versionDiffDetails').hidden=false;
  $('#versionDiffStatus').textContent=`已完成对比：${Number(visual.change_count||0).toLocaleString()} 个实体发生变化。`;
}
async function runVersionDiff() {
  if(!state.project||state.versions.filter(version=>version.has_dxf).length<2) return;
  const left=$('#versionLeft').value, right=$('#versionRight').value;
  if(!left||!right) { $('#versionDiffStatus').textContent='请同时选择左侧和右侧版本。'; return; }
  if(left===right) { $('#versionDiffStatus').textContent='左右版本必须不同。'; return; }
  const project=state.project; const button=$('#versionDiffButton'); button.disabled=true; $('#versionDiffStatus').textContent='正在生成视觉 diff…';
  try { const result=await request('/api/version-visual-diff',{project_id:project.id,left,right,width:1100,height:620}); renderVersionDiff(result); details(result); }
  catch(error) { $('#versionDiffStatus').textContent='生成失败：'+error.message; $('#versionDiffPreview').hidden=true; $('#versionDiffDetails').hidden=true; toast(error.message); }
  finally { button.disabled=false; }
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
async function previewAsset(path,viewerState=null) {
  const version=++state.preview;
  state.previewPath=path;
  $('#drawingTools').hidden=true;
  const query=new URLSearchParams({root:state.project.project_dir,path});
  $('#previewName').textContent=path.split('/').pop(); $('#previewHint').textContent='正在读取…';
  $('#preview').innerHTML='<div class="empty">正在准备预览…</div>';
  $('#downloadAsset').href='/api/download?'+query; $('#downloadAsset').hidden=false;
  document.querySelectorAll('[data-asset]').forEach(b=>b.classList.toggle('active',b.dataset.asset===path));
  try {
    const suffix=path.split('.').pop().toLowerCase();
    if(suffix==='png') {
      const image=document.createElement('img'); image.src='/api/download?'+query; image.alt='工程图 PNG'; image.style.width='100%';
      $('#preview').replaceChildren(image); $('#previewHint').textContent='已导出的工程图；原图编辑后需重新导出。'; return;
    }
    if(suffix==='pdf') {
      $('#preview').innerHTML='<div class="empty">PDF 已保存，可点击下载后查看或打印。</div>';
      $('#previewHint').textContent='工程图 PDF 文件'; return;
    }
    if(!['step','stp','obj','dxf','csv','json'].includes(suffix)) {
      const data=await request('/api/artifact?'+query); if(version!==state.preview) return;
      const pre=document.createElement('pre'); pre.className='json'; pre.textContent=typeof data.content==='string'?data.content:JSON.stringify(data.content,null,2);
      $('#preview').replaceChildren(pre); $('#assetDetails').textContent=JSON.stringify(data,null,2); $('#previewHint').textContent='文本预览；可下载原始文件。'; return;
    }
    const data=await request('/api/asset-view?'+query); if(version!==state.preview) return;
    const frame=document.createElement('iframe'); frame.className=suffix==='dxf'?'preview-frame cad-frame':'preview-frame'; frame.title='工程资产预览'; frame.setAttribute('sandbox','allow-scripts');
    const interactive=data.viewer.includes('engineering-3d-viewer') || (suffix==='dxf' && data.viewer.includes('data-cad-viewer'));
    frame.srcdoc='<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\';"><style>body{margin:0;font:14px system-ui;background:#f7f9fc}svg{width:100%;height:430px}button{cursor:pointer}</style>'+(interactive?data.viewer:data.svg);
    if(viewerState&&suffix==='dxf') frame.addEventListener('load',()=>frame.contentWindow.postMessage({type:'cad-view-restore',assetPath:path,state:viewerState},'*'),{once:true});
    $('#preview').replaceChildren(frame); $('#assetDetails').textContent=JSON.stringify(data.asset,null,2);
    if(suffix==='dxf') configureDrawing(data.asset.cad_document,path);
    $('#previewHint').textContent=suffix==='dxf'?'原生几何视图 · 滚轮缩放、拖动平移、Shift 框选；图纸排版请查看出图预览':data.preview_note||(interactive?'拖动旋转 · 滚轮缩放 · 双击重置':'文件数据预览');
  } catch(error) { if(version!==state.preview) return; $('#preview').textContent=error.message; $('#previewHint').textContent='预览失败，仍可下载原始文件。'; }
}
function configureDrawing(drawing,path) {
  const previous=state.drawingPath===path?$('#plotLayout').value:'Model';
  state.drawing=drawing; state.drawingPath=path;
  $('#plotLayout').innerHTML='<option value="Model">模型空间</option>'+Object.keys(drawing.sheets||{}).map(name=>`<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join('');
  $('#plotLayout').value=previous in (drawing.sheets||{})?previous:'Model';
  $('#drawingTools').hidden=false;
  $('#plotResult').replaceChildren(); $('#drawingStatus').textContent='';
  updateDrawingLayout();
}
function updateDrawingLayout(loadFields=true) {
  const name=$('#plotLayout').value, model=name==='Model';
  for(const id of ['plotPaper','plotOrientation','plotScale']) $('#'+id).disabled=!model;
  if(!model) $('#plotScale').value='1:1';
  const fields=state.drawing?.sheets?.[name]?.title_block?.fields||{};
  if(loadFields) { $('#titleText').value=fields.TITLE||state.drawing?.name?.replace(/\.dxf$/i,'')||'';
  $('#titleNumber').value=fields.NUMBER||''; $('#titleRevision').value=fields.REV||'A'; }
  $('#saveTitle').disabled=model;
  $('#prepareSheet').disabled=!model;
  $('#plotHint').textContent=model?'模型导出可选择比例；创建工程图纸会自动适配模型。未定义绘图单位时请用“适合纸张”。':'按图纸的实际纸张大小导出，保留视口比例；打印 PDF 时请选择“实际大小”。';
}
function titleFields() { return {TITLE:$('#titleText').value,NUMBER:$('#titleNumber').value,REV:$('#titleRevision').value}; }
function drawingError(error) {
  const messages={
    PLOT_SCALE_DOES_NOT_FIT:'当前比例放不下，请选择更大纸张、更小比例，或“适合纸张”。',
    PLOT_MISSING_GLYPHS:'图纸字体缺少部分字符。请安装匹配字体并调整图纸文字样式后重试。',
    PLOT_DRAWING_UNITS_REQUIRED:'此 DXF 没有定义绘图单位，请先使用“适合纸张”。',
    PLOT_UNSUPPORTED_ENTITIES:'图纸包含暂不支持出图的对象，请查看详细错误信息。',
    PLOT_NO_VISIBLE_GEOMETRY:'当前图纸没有可见的可打印内容。',
    PLOT_SOURCE_CONFLICT:'原图在导出期间被修改，请刷新后重新导出。',
    PLOT_IMAGE_TOO_LARGE:'图像尺寸过大，请降低清晰度或缩小纸张。',
  };
  const key=Object.keys(messages).find(code=>error.message.includes(code));
  return key?messages[key]:error.message;
}
async function changeDrawing(prepare) {
  if(state.busy||!state.project||!state.drawing) return;
  const project=state.project, path=state.previewPath, version=state.preview;
  let name=$('#plotLayout').value, operation, arguments_;
  if(prepare) {
    name=$('#plotPaper').value;
    let count=2; while(state.drawing.sheets?.[name]) name=$('#plotPaper').value+'-'+count++;
    operation='cad.sheet_prepare'; arguments_={name,paper:$('#plotPaper').value,orientation:$('#plotOrientation').value,fields:titleFields()};
  } else {
    if(name==='Model') return;
    const existing=state.drawing.sheets?.[name]?.title_block;
    operation=existing?'cad.title_block_update':'cad.title_block_create';
    const values=titleFields();
    // Updating an external template changes only fields present in that template.
    arguments_={sheet:name,fields:existing?Object.fromEntries(Object.entries(values).filter(([key])=>key in existing.fields)):values};
  }
  busy(true); $('#drawingStatus').textContent='正在保存图纸…';
  try {
    const result=await request('/api/cad/operation',{operation,arguments:{...arguments_,project_dir:project.project_dir,input_file:path,output_file:path,auto_repair:false}});
    if(state.project?.id!==project.id||state.preview!==version) return;
    await previewAsset(path); $('#plotLayout').value=name; updateDrawingLayout();
    $('#drawingTools').open=true; $('#drawingStatus').textContent=prepare?'工程图纸已创建，可预览或导出；撤销可恢复创建前的图纸。':'标题栏已保存并验证。';
    details(result); await refreshAssets();
  } catch(error) { if(state.project?.id===project.id&&state.preview===version) {$('#drawingStatus').textContent='保存失败：'+drawingError(error); details({error:error.message}); toast(drawingError(error));} }
  finally {busy(false); if(state.drawing) updateDrawingLayout(false);}
}
async function exportDrawing(format,preview=false) {
  if(state.busy||!state.project||!state.drawing) return;
  const project=state.project, source=state.previewPath, version=state.preview;
  const layout=$('#plotLayout').value, model=layout==='Model';
  const basename=source.split('/').pop().replace(/\.dxf$/i,'');
  const safeLayout=layout.replace(/[^a-zA-Z0-9_\u4e00-\u9fff-]/g,'_');
  const output='exports/'+basename+'-'+safeLayout+'-'+Date.now()+'.'+format;
  const arguments_={project_dir:project.project_dir,input_file:source,output_file:output,layout,
    paper:model?$('#plotPaper').value:'layout',orientation:$('#plotOrientation').value,
    scale:model?$('#plotScale').value:'1:1',dpi:Number($('#plotDpi').value),color_mode:$('#plotColor').value};
  busy(true); $('#drawingStatus').textContent=preview?'正在生成出图预览…':'正在导出工程图…';
  try {
    const result=await request('/api/cad/operation',{operation:'cad.plot',arguments:arguments_});
    if(state.project?.id!==project.id||state.preview!==version) return;
    const query=new URLSearchParams({root:project.project_dir,path:result.relative_path});
    const link=document.createElement('a'); link.className='btn btn-light btn-link plot-download'; link.href='/api/download?'+query;
    link.textContent='下载 '+format.toUpperCase(); link.download=output.split('/').pop();
    const reportLink=document.createElement('a'); reportLink.className='plot-download'; reportLink.textContent='出图记录';
    reportLink.href='/api/download?'+new URLSearchParams({root:project.project_dir,path:result.relative_path+'.plot.json'});
    $('#plotResult').replaceChildren(link,reportLink);
    if(format==='png') {
      const image=document.createElement('img'); image.src=link.href; image.alt='工程图出图预览'; $('#plotResult').append(image);
    }
    const report=result.report;
    $('#drawingStatus').textContent=`已导出 ${report.page_mm.width.toFixed(1)} × ${report.page_mm.height.toFixed(1)} mm · ${report.warnings.length?'有 '+report.warnings.length+' 条出图提示，请查看出图记录':'文件校验通过'}。原图后续修改需重新导出。`;
    details(result); await refreshAssets();
  } catch(error) { if(state.project?.id===project.id&&state.preview===version) {$('#drawingStatus').textContent='导出失败：'+drawingError(error); details({error:error.message}); toast(drawingError(error));} }
  finally { busy(false); if(state.drawing) updateDrawingLayout(false); }
}
$('#plotLayout').onchange=()=>{updateDrawingLayout();$('#plotResult').replaceChildren();};
$('#plotPreview').onclick=()=>exportDrawing('png',true);
$('#plotPdf').onclick=()=>exportDrawing('pdf');
$('#plotPng').onclick=()=>exportDrawing('png');
$('#prepareSheet').onclick=()=>changeDrawing(true);
$('#saveTitle').onclick=()=>changeDrawing(false);
window.addEventListener('message', async event=>{
  const data=event.data;
  if(!data||data.type!=='cad-operation') return;
  const activeFrame=$('#preview iframe');
  if(!activeFrame || !event.source || event.source!==activeFrame.contentWindow) return;
  const reply=payload=>event.source.postMessage({type:'cad-operation-result',requestId:data.requestId,...payload},'*');
  if(state.busy) { reply({ok:false,error:'另一项操作正在保存，请稍候再试'}); return; }
  const assetPath=String(data.assetPath||'');
  if(!state.project||assetPath!==state.previewPath||!assetPath.toLowerCase().endsWith('.dxf')) {
    reply({ok:false,error:'当前预览文件已变更，请重新打开 DXF'});
    return;
  }
  const operation=String(data.operation||'');
  const allowed=new Set(['cad.entity_move','cad.entity_rotate','cad.entity_delete','cad.entity_update','cad.selection_create','cad.undo','cad.redo']);
  if(!allowed.has(operation)) {
    reply({ok:false,error:'当前视图不支持此操作'});
    return;
  }
  const projectDir=String(state.project.project_dir||'');
  const projectId=state.project.id, previewVersion=state.preview;
  const inputFile=projectDir.replace(/[\\/]$/,'')+'/'+assetPath;
  try {
    busy(true);
    const result=await request('/api/cad/operation',{operation,arguments:{...(data.arguments||{}),auto_repair:false,project_dir:projectDir,input_file:inputFile,output_file:inputFile}});
    const deleted=result.document===null;
    reply({ok:true,deleted,history:result.history});
    toast(result.persisted_validation?.valid?'CAD 修改已保存并验证':'已恢复原始文件；原文件仍有几何验证问题');
    if(state.project?.id===projectId && state.preview===previewVersion) {
      if(!deleted) await previewAsset(assetPath,data.viewerState);
      else {$('#downloadAsset').hidden=true;$('#previewHint').textContent='文件创建已撤销；可点击重做恢复。';}
      await refreshAssets();
    }
  } catch(error) {
    reply({ok:false,error:error.message});
    toast(error.message);
  } finally { busy(false); }
});
async function refreshDoctor() {
  $('#doctor').textContent='正在检查软件配置和连接…';
  const [capabilities,items]=await Promise.all([request('/api/capabilities'),request('/api/doctor')]);
  $('#capabilities').innerHTML=(capabilities.modes||[]).map(mode=>`<div class="status-card mode-card"><strong>${escapeHTML(mode.label)}</strong><p class="muted">${escapeHTML(mode.reason)}</p><span class="pill ${mode.ready?'green':'amber'}">${mode.ready?'可用':'未就绪'}</span></div>`).join('');
  $('#doctor').innerHTML=items.map(item=>`<div class="status-card"><strong>${escapeHTML(item.tool||item.name||'环境')}</strong><p class="muted">${escapeHTML(item.message||item.reason||item.status||'查看详情')}</p><span class="pill ${item.available?'green':'amber'}">${item.execution_ready?'启动程序已找到 · 实机待验收':item.reachable?'连接正常 · 需绑定动作':item.available?'可用':'未就绪 / 需配置'}</span><details><summary>诊断详情</summary><pre class="json">${escapeHTML(JSON.stringify(item,null,2))}</pre></details></div>`).join('');
}
document.addEventListener('click',event=>{
  const target=event.target.closest('button'); if(!target||target.disabled) return;
  if(target.dataset.panel) choosePanel(target.dataset.panel);
  if(target.dataset.action==='new-project') openWizard();
  if(target.dataset.action==='demo') runOffline(true);
  if(target.dataset.project) openProject(target.dataset.project).catch(reportError);
  if(target.dataset.asset) previewAsset(target.dataset.asset).catch(reportError);
  if(target.dataset.versionView) inspectVersion(target.dataset.versionView).catch(reportError);
  if(target.dataset.versionAsset) inspectVersion(target.dataset.versionId,Number(target.dataset.versionAsset)).catch(reportError);
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
$('#refreshVersions').onclick=()=>refreshVersions().catch(reportError);
$('#createVersion').onclick=()=>createVersionSnapshot().catch(reportError);
$('#versionLeft').onchange=()=>{ $('#versionDiffStatus').textContent=''; };
$('#versionRight').onchange=()=>{ $('#versionDiffStatus').textContent=''; };
$('#versionDiffButton').onclick=()=>runVersionDiff().catch(reportError);
$('#closeVersionInspect').onclick=()=>{$('#versionInspect').hidden=true;};
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
const taskStatusLabels = {...labels, waiting:'等待恢复连接', planned:'排练通过', blocked:'依赖阻塞', unavailable:'不可用', rolled_back:'已回滚'};
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
  $('#resumeTask').hidden=!report.resume_allowed;
  $('#resumeTask').disabled=state.busy;
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
  const eventNames={workflow:'工程链已记录',workflow_step:'工程链步骤已完成',waiting:'等待恢复连接',queued:'任务已创建',running:'开始执行',plan:'计划已记录',stage_started:'阶段开始',stage_finished:'阶段结束',completed:'任务已完成',failed:'任务失败',execution_mode:'运行模式已记录',software_job:'软件任务状态'};
  $('#taskEvents').innerHTML=report.events.map(e=>`<li><time>${escapeHTML(displayTime(e.created_at))}</time><div><strong>${escapeHTML(eventNames[e.event]||e.event)}</strong><p>${escapeHTML(e.payload.label||e.payload.error||(e.event==='software_job'?`${e.payload.tool} · ${e.payload.status} · ${e.payload.request_id}`:''))}${e.payload.duration_seconds==null?'':` · ${escapeHTML(displayDuration(e.payload.duration_seconds))}`}</p></div></li>`).join('');
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

const softwareJobsState={tool:'catia',jobs:[],receipts:[],cursor:null,logId:null,logOffset:0,logDecoder:new TextDecoder(),releaseId:null,busy:false,view:0};
const softwareActionLabels={create_or_update_model:'创建或更新 CATIA 模型',export_step:'导出 STEP 模型',mesh_and_solve:'Ansys 网格与求解',build_and_run_multibody:'Adams 多体仿真',run_vehicle_dynamics:'VI-grade 车辆动力学'};
const softwareJobLabels={accepted:'已接收',queued:'等待软件空闲',running:'软件运行中',cancelling:'正在停止启动程序',completed:'结果已生成',failed:'执行失败',cancelled:'已取消',interrupted:'任务管理进程中断'};
async function softwareJobRequest(action,extra={}) {
  return request('/api/software-jobs',{tool:softwareJobsState.tool,action,...(state.project?{project_id:state.project.id}:{}),...extra});
}
function softwareNotice(message) { $('#softwareJobNotice').textContent=message; }
async function refreshSoftwareJobs(more=false) {
  if(softwareJobsState.busy) return;
  softwareJobsState.busy=true; $('#refreshSoftwareJobs').disabled=true; $('#moreSoftwareJobs').disabled=true;$('#softwareJobTool').disabled=true;
  const view=++softwareJobsState.view;
  softwareJobsState.tool=$('#softwareJobTool').value;
  try {
    const response=await softwareJobRequest('list',{limit:20,...(more&&softwareJobsState.cursor?{cursor:softwareJobsState.cursor}:{})});
    const receipts=state.project?await softwareJobRequest('receipts'): {jobs:[]};
    if(view!==softwareJobsState.view) return;
    softwareJobsState.jobs=more?[...softwareJobsState.jobs,...response.jobs]:response.jobs;
    softwareJobsState.receipts=receipts.jobs; softwareJobsState.cursor=response.next_cursor;
    renderSoftwareJobs();
    softwareNotice(state.project?`结果回收项目：${state.project.name}。任务完成表示文件已生成，工程指标仍需检查。`:'尚未打开项目；可以查看和取消任务，回收结果需先打开原项目。');
  } catch(error) { softwareNotice(error.message); }
  finally {softwareJobsState.busy=false;$('#refreshSoftwareJobs').disabled=false;$('#moreSoftwareJobs').disabled=false;$('#softwareJobTool').disabled=false;}
}
function renderSoftwareJobs() {
  $('#moreSoftwareJobs').hidden=!softwareJobsState.cursor;
  $('#softwareJobs').innerHTML=softwareJobsState.jobs.length?softwareJobsState.jobs.map(job=>{
    const receipt=softwareJobsState.receipts.find(r=>r.request_id===job.request_id);
    const active=['accepted','queued','running','cancelling'].includes(job.status);
    const identifier=escapeHTML(job.request_id);
    return `<article class="software-job" data-software-job="${identifier}"><div class="software-job-heading"><strong>${escapeHTML(softwareActionLabels[job.action]||job.action)}</strong><span class="pill ${job.status==='completed'?'green':'amber'}">${escapeHTML(softwareJobLabels[job.status]||job.status)}</span></div><p class="muted software-job-id">${identifier}</p>${job.requires_review?'<p class="software-review-note">软件是否停止尚待核实。占用已保留，后续同软件任务继续等待。</p>':''}${active&&job.software_lease&&job.software_lease!==job.request_id?`<p class="muted">等待任务 ${escapeHTML(job.software_lease)}</p>`:''}<div class="file-actions"><button class="btn btn-light" data-software-action="logs" data-job="${identifier}">查看日志</button>${active?`<button class="btn btn-light" data-software-action="cancel" data-job="${identifier}">取消任务</button>`:''}${job.status==='completed'&&receipt&&!receipt.collected&&!receipt.workflow_id?`<button class="btn btn-primary" data-software-action="collect" data-job="${identifier}">回收结果到项目</button>`:''}${receipt?.workflow_id&&!receipt.collected?'<span class="pill">请在工程链中继续</span>':''}${receipt?.collected?'<span class="pill green">结果已回收</span>':''}${job.requires_review&&['failed','cancelled','interrupted'].includes(job.status)?`<button class="btn btn-light" data-software-action="release" data-job="${identifier}">核实停止状态</button>`:''}</div></article>`;
  }).join(''):'<p class="muted">这个软件尚无任务记录。</p>';
}
async function readSoftwareLogs(reset=false) {
  if(!softwareJobsState.logId) return;
  if(reset) {softwareJobsState.logOffset=0;softwareJobsState.logDecoder=new TextDecoder();$('#softwareJobLogText').textContent='';}
  const identifier=softwareJobsState.logId,view=softwareJobsState.view;
  const result=await softwareJobRequest('logs',{request_id:identifier,stream:$('#softwareLogStream').value,offset:softwareJobsState.logOffset,limit:16384});
  if(identifier!==softwareJobsState.logId||view!==softwareJobsState.view) return;
  softwareJobsState.logOffset=result.next_offset;
  const bytes=Uint8Array.from(atob(result.content_base64||''),c=>c.charCodeAt(0));
  const finished=result.eof&&['completed','failed','cancelled','interrupted'].includes(result.status);
  $('#softwareJobLogText').textContent+=softwareJobsState.logDecoder.decode(bytes,{stream:!finished});
  $('#softwareJobLogTitle').textContent=$('#softwareJobLogText').textContent?'运行日志 · '+identifier:'运行日志 · 暂无输出';
}
$('#refreshSoftwareJobs').onclick=()=>refreshSoftwareJobs();
$('#moreSoftwareJobs').onclick=()=>refreshSoftwareJobs(true);
$('#softwareJobTool').onchange=()=>{
  softwareJobsState.view++;softwareJobsState.jobs=[];softwareJobsState.logId=null;softwareJobsState.releaseId=null;softwareJobsState.cursor=null;
  $('#softwareJobs').textContent='';$('#softwareJobLogs').hidden=true;$('#softwareReleaseForm').hidden=true;
  refreshSoftwareJobs();
};
$('#refreshSoftwareLogs').onclick=()=>readSoftwareLogs().catch(error=>softwareNotice(error.message));
$('#softwareLogStream').onchange=()=>readSoftwareLogs(true).catch(error=>softwareNotice(error.message));
$('#dismissSoftwareRelease').onclick=()=>{$('#softwareReleaseForm').hidden=true;softwareJobsState.releaseId=null;};
$('#softwareJobs').onclick=async event=>{
  const button=event.target.closest('[data-software-action]'); if(!button) return;
  const action=button.dataset.softwareAction,identifier=button.dataset.job;
  if(action==='release') {softwareJobsState.releaseId=identifier;$('#softwareReleaseForm').reset();$('#softwareReleaseForm').hidden=false;return;}
  button.disabled=true;
  try {
    if(action==='logs') {softwareJobsState.logId=identifier;$('#softwareJobLogs').hidden=false;await readSoftwareLogs(true);}
    else {
      await softwareJobRequest(action,{request_id:identifier});
      await refreshSoftwareJobs();
      softwareNotice(action==='cancel'?'取消请求已记录。软件会话是否停止请以工作站检查和任务状态为准。':'结果已回收到项目，并保存历史副本。');
    }
  } catch(error) {softwareNotice(error.message);} finally {button.disabled=false;}
};
$('#softwareReleaseForm').onsubmit=async event=>{
  event.preventDefault();
  if(!softwareJobsState.releaseId||!$('#softwareStopped').checked) return;
  const submit=$('#softwareReleaseForm button[type="submit"]');submit.disabled=true;
  try {
    await softwareJobRequest('release',{request_id:softwareJobsState.releaseId,confirmed_stopped:true,reason:$('#softwareReleaseReason').value});
    $('#softwareReleaseForm').hidden=true;softwareJobsState.releaseId=null;
    await refreshSoftwareJobs();softwareNotice('核实记录已保存，排队任务可以继续。');
  } catch(error) {softwareNotice(error.message);} finally {submit.disabled=false;}
};

const savedWorkflowLabels={ready:'已保存',running:'上次记录：执行中',waiting:'等待恢复连接',blocked:'需要处理问题',failed:'步骤失败',completed:'已完成',publishing:'正在保存结果',not_started:'尚未开始',submitting:'正在提交'};
let workflowRefreshing=false;
async function refreshWorkflows() {
  if(workflowRefreshing) return;
  const project=state.project;
  if(!project) {$('#workflowNotice').textContent='请先打开原项目。';return;}
  workflowRefreshing=true;$('#refreshWorkflows').disabled=true;
  try {
    const result=await request('/api/workflows',{project_id:project.id,action:'list'});
    if(state.project?.id!==project.id) return;
    $('#savedWorkflows').innerHTML=result.workflows.length?result.workflows.map(w=>`<article class="software-job"><div class="software-job-heading"><strong>${escapeHTML(w.prompt)}</strong><span class="pill ${w.status==='completed'?'green':'amber'}">${escapeHTML(savedWorkflowLabels[w.status]||w.status)}</span></div><p class="muted">${escapeHTML(w.workflow_id)}</p><ol>${w.steps.map(s=>`<li>${escapeHTML(s.tool)} · ${escapeHTML(s.objective||s.action)} · ${escapeHTML(savedWorkflowLabels[s.status]||s.status)}</li>`).join('')}</ol>${w.error?`<p class="${w.status==='waiting'?'muted':'error'}">${escapeHTML(w.error.code==='REMOTE_JOB_PENDING'?'本次等待已结束，工作站上的任务可能仍在运行。点击恢复可继续查询原任务。':(w.error.message||w.error.code))}</p>`:''}${w.status!=='completed'?`<button class="btn btn-blue" data-resume-workflow="${escapeHTML(w.workflow_id)}" data-workflow-project="${escapeHTML(project.id)}">恢复并继续剩余步骤</button>`:''}${w.status==='completed'&&w.repair_available?`<button class="btn btn-light" data-repair-workflow="${escapeHTML(w.workflow_id)}" data-workflow-project="${escapeHTML(project.id)}">按失败证据尝试修复</button>`:''}</article>`).join(''):'<p class="muted">本项目还没有需要恢复的工程链。</p>';
    $('#workflowNotice').textContent='显示已保存的进度。恢复时会重新核对软件任务、输入和结果；软件仍忙时继续等待。';
  } catch(error) {$('#workflowNotice').textContent=error.message;}
  finally {workflowRefreshing=false;$('#refreshWorkflows').disabled=false;}
}
$('#refreshWorkflows').onclick=()=>refreshWorkflows();
$('#savedWorkflows').onclick=async event=>{
  const repair=event.target.closest('[data-repair-workflow]');
  const button=event.target.closest('[data-resume-workflow]');
  if(repair) {
    if(repair.dataset.workflowProject!==state.project?.id) return;
    repair.disabled=true;busy(true);$('#workflowNotice').textContent='正在根据失败指标生成有界修复方案；原工程链不会被覆盖。';
    try { const result=await request('/api/workflows',{project_id:state.project.id,action:'repair',workflow_id:repair.dataset.repairWorkflow}); details(result); toast(result.status==='committed'?'修复方案已执行并保存新版本。':'没有可自动执行的修复策略，请人工复核。'); }
    catch(error) {$('#workflowNotice').textContent=error.message;}
    finally {busy(false);repair.disabled=false;await refreshWorkflows();await refreshTasks();}
    return;
  }
  if(!button||state.busy) return;
  if(button.dataset.workflowProject!==state.project?.id) {await refreshWorkflows();return;}
  button.disabled=true;busy(true);let failure='';
  $('#workflowNotice').textContent='正在查询原任务并继续工程链；耗时求解可能仍需等待。';
  try {
    const result=await request('/api/workflows',{project_id:state.project.id,action:'resume',workflow_id:button.dataset.resumeWorkflow});
    details(result);
    toast(result.status==='committed'?'工程链已完成，结果和历史已保存。':'原任务仍在等待或有问题待处理，请查看记录。');
  } catch(error) {failure=error.message;}
  finally {busy(false);button.disabled=false;await refreshWorkflows();await refreshTasks();if(failure) $('#workflowNotice').textContent=failure;}
};
$('#resumeTask').onclick=async()=>{
  if(state.busy||!state.taskId) return;
  const identifier=state.taskId;busy(true);
  try {await request('/api/tasks/'+encodeURIComponent(identifier)+'/resume',{});}
  catch(error) {reportError(error);}
  finally {busy(false);await refreshTasks();if(state.taskId===identifier) await showTask(identifier,true);}
};

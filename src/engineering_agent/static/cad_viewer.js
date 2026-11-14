const svg=document.getElementById('cad'), selected=new Set(), status=document.getElementById('selection'), editStatus=document.getElementById('editStatus');
const snapToggle=document.getElementById('snap'), snapMode=document.getElementById('snapMode'), snapStatus=document.getElementById('snapStatus');
const viewer=document.querySelector('[data-cad-viewer]'), assetPath=viewer?.dataset.assetPath||'';
let pending=null, requestSequence=0, drag=null, gripDrag=null, suppressClick=false, spaceDown=false, snapMarker=null, activeSnap=null, gripLayer=null, gripPreview=null;
let initialView=svg.dataset.initialViewbox.split(/\s+/).map(Number);
let worldMatrix=new DOMMatrix(JSON.parse(svg.dataset.worldTransform));
let inverseWorld=worldMatrix.inverse();
let primitives=[...svg.querySelectorAll('.entity:not(.pick-stroke)')];
let allPaths=[...svg.querySelectorAll('.entity')];
let layerInputs=[...document.querySelectorAll('input[data-layer]')];
let layerVisibility=new Map(layerInputs.map(input=>[input.dataset.layer,input.checked]));
const operationHistory=JSON.parse(document.getElementById('operationHistory')?.textContent||'[]');
const operationButtons=[...document.querySelectorAll('[data-history-index]')];
let historyState={can_undo:!document.getElementById('undo').disabled,can_redo:!document.getElementById('redo').disabled};
const revisionHistory=JSON.parse(document.getElementById('revisionHistory')?.textContent||'{}');
const revisions=revisionHistory.revisions||[], frames=revisions.length?[
  {revision_id:revisions[0].id,side:'before',label:'初始图纸'},
  ...revisions.map((row,index)=>({revision_id:row.id,side:'after',label:`${index+1}. ${row.operation}${row.applied?'':' · 尚未重做'} · ${row.at}`})),
]:[];
const revisionSelect=document.getElementById('revision'), historyStatus=document.getElementById('historyStatus');
let readOnly=viewer.dataset.readOnly==='true', historyIndex=-1, historyPending=null, historyResolve=null, historySequence=0, batchPending=null, batchSequence=0, playSequence=0, playing=false, currentView=null;
for(const [index,item] of frames.entries()){const option=document.createElement('option');option.value=String(index);option.textContent=item.label;revisionSelect.append(option);}
function refreshControls(){
  document.querySelectorAll('.edits button,.edits input').forEach(el=>el.disabled=readOnly||pending!==null);
  document.querySelectorAll('.query-controls button,.query-controls input').forEach(el=>el.disabled=readOnly||pending!==null);
  document.getElementById('undo').disabled=readOnly||pending!==null||!historyState.can_undo;
  document.getElementById('redo').disabled=readOnly||pending!==null||!historyState.can_redo;
  const unavailable=!frames.length||!assetPath||pending!==null;
  revisionSelect.disabled=unavailable;
  document.getElementById('historyPrevious').disabled=unavailable||historyPending!==null||historyIndex===0;
  document.getElementById('historyNext').disabled=unavailable||historyPending!==null||(historyIndex<0?revisionHistory.cursor:historyIndex)>=frames.length-1;
  document.getElementById('historyCurrent').disabled=!readOnly||!assetPath||pending!==null;
  document.getElementById('replay').disabled=unavailable;
  document.getElementById('replay').textContent=playing?'暂停':'播放历史';
  document.querySelector('.operations').hidden=false;
  viewer.dataset.readOnly=String(readOnly);
}
function entityData(id){
  const element=primitives.find(item=>item.dataset.entityId===id);
  if(!element)return null;
  try{return JSON.parse(element.dataset.entityJson||'{}');}catch{return null;}
}
function finitePoint(value,fallback={x:0,y:0,z:0}){
  const source=value&&typeof value==='object'?value:fallback;
  return {x:Number(source.x||0),y:Number(source.y||0),z:Number(source.z||0)};
}
function gripPoint(x,y,z=0){return {x:Number(x),y:Number(y),z:Number(z||0)};}
function polarPoint(center,radius,angle){
  const radians=Number(angle)*Math.PI/180;
  return gripPoint(center.x+radius*Math.cos(radians),center.y+radius*Math.sin(radians),center.z);
}
function gripDefinitions(entity){
  if(!entity)return [];
  const type=String(entity.type||'').toUpperCase(), center=finitePoint(entity.center), grips=[];
  const add=(kind,point,extra={})=>{if(Number.isFinite(point?.x)&&Number.isFinite(point?.y))grips.push({kind,point:finitePoint(point),...extra});};
  if(type==='LINE'){add('start',entity.start);add('end',entity.end);}
  else if(type==='LWPOLYLINE'||type==='POLYLINE'||type==='LEADER'){
    (entity.vertices||[]).forEach((point,index)=>add('vertex',point,{index}));
  } else if(type==='SPLINE'){
    const key=Array.isArray(entity.fit_points)?'fit_points':'control_points';
    (entity[key]||[]).forEach((point,index)=>add('vertex',point,{index,key}));
  } else if(type==='CIRCLE'){
    const radius=Math.abs(Number(entity.radius||0));add('center',center);add('radius',gripPoint(center.x+radius,center.y,center.z));
  } else if(type==='ARC'){
    const radius=Math.abs(Number(entity.radius||0));
    add('center',center);add('start',polarPoint(center,radius,Number(entity.start_angle||0)));
    add('end',polarPoint(center,radius,Number(entity.end_angle||0)));
    add('radius',polarPoint(center,radius,(Number(entity.start_angle||0)+Number(entity.end_angle||0))/2));
  } else if(type==='ELLIPSE'){
    const axis=finitePoint(entity.major_axis,{x:1,y:0,z:0}), length=Math.hypot(axis.x,axis.y,axis.z)||1;
    const ratio=Number(entity.ratio||1), ux=axis.x/length, uy=axis.y/length;
    add('center',center);add('major',gripPoint(center.x+axis.x,center.y+axis.y,center.z+axis.z));
    add('minor',gripPoint(center.x-uy*length*ratio,center.y+ux*length*ratio,center.z));
  } else if(type==='TEXT'||type==='MTEXT'||type==='INSERT')add('insert',entity.insert);
  return grips;
}
function ensureGripLayer(){
  if(gripLayer?.isConnected)return gripLayer;
  gripLayer=document.createElementNS('http://www.w3.org/2000/svg','g');
  gripLayer.setAttribute('class','cad-grips');svg.append(gripLayer);return gripLayer;
}
function worldToSvg(point){return new DOMPoint(point.x,point.y).matrixTransform(worldMatrix);}
function clearGripPreview(){gripPreview?.remove();gripPreview=null;}
function renderGrips(){
  const layer=ensureGripLayer();layer.replaceChildren();clearGripPreview();
  if(readOnly||pending!==null||selected.size!==1)return;
  const entityId=[...selected][0], entity=entityData(entityId);if(!entity)return;
  for(const definition of gripDefinitions(entity)){
    const point=worldToSvg(definition.point), group=document.createElementNS('http://www.w3.org/2000/svg','g');
    group.setAttribute('class','cad-grip');group.dataset.entityId=entityId;group.dataset.gripKind=definition.kind;
    if(definition.index!==undefined)group.dataset.gripIndex=String(definition.index);
    if(definition.key)group.dataset.gripKey=definition.key;
    group.setAttribute('transform',`translate(${point.x} ${point.y})`);group.setAttribute('role','button');
    group.setAttribute('aria-label',`${entity.type} ${definition.kind} grip`);
    const circle=document.createElementNS('http://www.w3.org/2000/svg','circle');circle.setAttribute('r','5');group.append(circle);layer.append(group);
  }
}
function patchForGrip(entity,definition,point){
  const type=String(entity?.type||'').toUpperCase(), p=finitePoint(point), patch={};
  if(type==='LINE')patch[definition.kind==='start'?'start':'end']=p;
  else if(type==='LWPOLYLINE'||type==='POLYLINE'||type==='LEADER'||type==='SPLINE'){
    const key=type==='SPLINE'?(definition.key||'fit_points'):'vertices';
    patch[key]=(entity[key]||[]).map((item,index)=>index===definition.index?p:finitePoint(item));
  } else if(type==='CIRCLE'){
    if(definition.kind==='center')patch.center=p;
    else patch.radius=Math.max(1e-9,Math.hypot(p.x-finitePoint(entity.center).x,p.y-finitePoint(entity.center).y));
  } else if(type==='ARC'){
    const center=finitePoint(entity.center), radius=Math.max(1e-9,Math.hypot(p.x-center.x,p.y-center.y));
    if(definition.kind==='center')patch.center=p;
    else {patch.radius=radius;const angle=Math.atan2(p.y-center.y,p.x-center.x)*180/Math.PI;if(definition.kind==='start')patch.start_angle=angle;else if(definition.kind==='end')patch.end_angle=angle;}
  } else if(type==='ELLIPSE'){
    const center=finitePoint(entity.center), axis=finitePoint(entity.major_axis,{x:1,y:0,z:0}), length=Math.hypot(axis.x,axis.y,axis.z)||1;
    if(definition.kind==='center')patch.center=p;
    else if(definition.kind==='major')patch.major_axis={x:p.x-center.x,y:p.y-center.y,z:p.z-center.z};
    else patch.ratio=Math.max(1e-9,Math.hypot(p.x-center.x,p.y-center.y)/length);
  } else if(type==='TEXT'||type==='MTEXT'||type==='INSERT')patch.insert=p;
  return patch;
}
function previewEntity(entity,patch){
  const updated={...entity,...patch}, type=String(updated.type||'').toUpperCase(), group=document.createElementNS('http://www.w3.org/2000/svg','g');
  group.setAttribute('class','grip-preview');
  const point=(value)=>worldToSvg(finitePoint(value)), setStroke=(element)=>{element.setAttribute('fill','none');element.setAttribute('vector-effect','non-scaling-stroke');group.append(element);};
  const screenRadius=(center,radius)=>{const origin=point(center),edge=point(gripPoint(center.x+radius,center.y,center.z));return Math.hypot(edge.x-origin.x,edge.y-origin.y);};
  if(type==='LINE'){
    const start=point(updated.start),end=point(updated.end),line=document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1',start.x);line.setAttribute('y1',start.y);line.setAttribute('x2',end.x);line.setAttribute('y2',end.y);setStroke(line);
  } else if(type==='LWPOLYLINE'||type==='POLYLINE'||type==='LEADER'||type==='SPLINE'){
    const points=(updated.vertices||updated.fit_points||updated.control_points||[]).map(point).map(item=>`${item.x},${item.y}`).join(' ');
    if(points){const poly=document.createElementNS('http://www.w3.org/2000/svg','polyline');poly.setAttribute('points',points);if(updated.closed)poly.setAttribute('data-closed','true');setStroke(poly);}
  } else if(type==='CIRCLE'){
    const center=point(updated.center), circle=document.createElementNS('http://www.w3.org/2000/svg','circle');circle.setAttribute('cx',center.x);circle.setAttribute('cy',center.y);circle.setAttribute('r',screenRadius(finitePoint(updated.center),Math.abs(Number(updated.radius||0))));setStroke(circle);
  } else if(type==='ARC'){
    const center=finitePoint(updated.center), radius=Math.abs(Number(updated.radius||0));
    const start=worldToSvg(polarPoint(center,radius,Number(updated.start_angle||0))),end=worldToSvg(polarPoint(center,radius,Number(updated.end_angle||0)));
    const sweep=Math.max(0,Number(updated.end_angle||0)-Number(updated.start_angle||0)), path=document.createElementNS('http://www.w3.org/2000/svg','path');
    const radiusOnScreen=screenRadius(center,radius);path.setAttribute('d',`M ${start.x} ${start.y} A ${radiusOnScreen} ${radiusOnScreen} 0 ${sweep>180?1:0} 1 ${end.x} ${end.y}`);setStroke(path);
  } else if(type==='ELLIPSE'){
    const center=point(updated.center), axis=finitePoint(updated.major_axis,{x:1,y:0,z:0}), length=Math.hypot(axis.x,axis.y)||1;
    const ellipse=document.createElementNS('http://www.w3.org/2000/svg','ellipse');ellipse.setAttribute('cx',center.x);ellipse.setAttribute('cy',center.y);ellipse.setAttribute('rx',screenRadius(finitePoint(updated.center),length));ellipse.setAttribute('ry',screenRadius(finitePoint(updated.center),length*Math.abs(Number(updated.ratio||1))));setStroke(ellipse);
  }
  return group;
}
function updateGripPreview(entity,definition,point){
  clearGripPreview();const patch=patchForGrip(entity,definition,point);if(!Object.keys(patch).length)return;
  gripPreview=previewEntity(entity,patch);ensureGripLayer().append(gripPreview);
  const marker=worldToSvg(point), circle=document.createElementNS('http://www.w3.org/2000/svg','circle');circle.setAttribute('class','grip-preview-point');circle.setAttribute('cx',marker.x);circle.setAttribute('cy',marker.y);circle.setAttribute('r','5');gripPreview.append(circle);
  return patch;
}
function beginGripDrag(event,grip){
  if(readOnly||pending!==null||selected.size!==1)return;
  const id=grip.dataset.entityId, entity=entityData(id), definition=gripDefinitions(entity).find(item=>item.kind===grip.dataset.gripKind&&String(item.index??'')===String(grip.dataset.gripIndex??''));
  if(!entity||!definition)return;
  event.preventDefault();event.stopPropagation();selected.clear();selected.add(id);paint();
  gripDrag={id,entity,definition,pointerId:event.pointerId,start:definition.point,current:definition.point,patch:{}};
  svg.setPointerCapture(event.pointerId);editStatus.textContent='拖动夹点预览，松开后提交…';
}
function finishGripDrag(event,cancel=false){
  if(!gripDrag)return;
  const current=gripDrag, distance=Math.hypot(current.current.x-current.start.x,current.current.y-current.start.y);
  clearGripPreview();hideSnap();if(svg.hasPointerCapture(current.pointerId))svg.releasePointerCapture(current.pointerId);gripDrag=null;
  if(cancel||distance<1e-9){renderGrips();return;}
  requestEdit('cad.entity_update',{entity_id:current.id,patch:current.patch,source:'viewer-grip',grip_kind:current.definition.kind});
}
function paint(){svg.querySelectorAll('.entity').forEach(e=>e.classList.toggle('selected',selected.has(e.dataset.entityId)));status.textContent=selected.size?`已选择 ${selected.size} 个实体：${[...selected].join(', ')}`:'未选择实体';renderGrips();}
function selectedIds(){return [...selected];}
function requestEdit(operation, arguments_){
  if(readOnly){editStatus.textContent='历史图纸只读，请先返回当前文件';return;}
  if(pending)return;
  if(window.parent===window){editStatus.textContent='请在工作台中打开文件后编辑';return;}
  if(!['cad.undo','cad.redo'].includes(operation) && !selected.size){editStatus.textContent='请先选择实体';return;}
  if(operation==='cad.entity_update' && selected.size!==1){editStatus.textContent='属性编辑一次只能选择一个实体';return;}
  if(operation==='cad.entity_update'&&arguments_.source!=='viewer-grip'){
    const item=[...svg.querySelectorAll('.entity')].find(e=>selected.has(e.dataset.entityId));
    if(!['TEXT','MTEXT'].includes(item?.dataset.entityType)){editStatus.textContent='请选择 TEXT 或 MTEXT 文字实体';return;}
  }
  const numbers=operation==='cad.entity_move'?arguments_.delta:operation==='cad.entity_rotate'?[arguments_.angle,arguments_.center.x,arguments_.center.y]:[];
  if(numbers.some(value=>!Number.isFinite(value)) || [...document.querySelectorAll('input[type=number]')].some(input=>!input.validity.valid)){editStatus.textContent='请输入有效数字';return;}
  editStatus.textContent='正在提交…';
  const ids=selectedIds(), payload={...arguments_, entity_ids:ids};
  if(operation==='cad.entity_update') payload.entity_id=ids[0];
  pending=++requestSequence;
  document.querySelectorAll('button').forEach(button=>button.disabled=true);
  window.parent.postMessage({type:'cad-operation', requestId:pending, assetPath, operation, arguments:payload, viewerState:captureView()}, '*');
}
function viewBox(){return svg.getAttribute('viewBox').split(/\s+/).map(Number);}
function setView(box){
  if(box.length!==4||box.some(v=>!Number.isFinite(v))||box[2]<=0||box[3]<=0)return;
  if(box[2]<initialView[2]*1e-6||box[2]>initialView[2]*1e6)return;
  svg.setAttribute('viewBox',box.join(' '));
  document.getElementById('zoom').textContent=(initialView[2]/box[2]*100).toFixed(0)+'%';
}
function localPoint(event,matrix=svg.getScreenCTM().inverse()){
  return new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix);
}
function visiblePaths(){return primitives.filter(el=>el.style.display!=='none');}
function ensureSnapMarker(){
  if(snapMarker?.isConnected)return snapMarker;
  snapMarker=document.createElementNS('http://www.w3.org/2000/svg','g');snapMarker.setAttribute('class','snap-marker-group');snapMarker.style.display='none';
  const circle=document.createElementNS('http://www.w3.org/2000/svg','circle');circle.setAttribute('class','snap-marker');circle.setAttribute('r','4');
  const horizontal=document.createElementNS('http://www.w3.org/2000/svg','line');horizontal.setAttribute('class','snap-marker-line');horizontal.setAttribute('x1','-7');horizontal.setAttribute('x2','7');
  const vertical=document.createElementNS('http://www.w3.org/2000/svg','line');vertical.setAttribute('class','snap-marker-line');vertical.setAttribute('y1','-7');vertical.setAttribute('y2','7');
  snapMarker.append(circle,horizontal,vertical);svg.append(snapMarker);return snapMarker;
}
function hideSnap(){
  activeSnap=null;if(snapMarker)snapMarker.style.display='none';if(snapStatus)snapStatus.textContent='';
}
function snapCandidates(){
  const candidates=[];const seen=new Set();
  const add=(x,y,kind,id)=>{
    if(!Number.isFinite(x)||!Number.isFinite(y)||!id)return;
    const key=`${id}:${kind}:${x.toFixed(6)}:${y.toFixed(6)}`;if(seen.has(key))return;seen.add(key);candidates.push({x,y,kind,entityId:id});
  };
  for(const element of visiblePaths()){
    const id=element.dataset.entityId;if(!id)continue;
    let box;try{box=element.getBBox();}catch{continue;}
    add(box.x+box.width/2,box.y+box.height/2,'center',id);
    add(box.x,box.y,'endpoint',id);add(box.x+box.width,box.y,'endpoint',id);
    add(box.x,box.y+box.height,'endpoint',id);add(box.x+box.width,box.y+box.height,'endpoint',id);
    if(typeof element.getTotalLength==='function'){
      try{
        const length=element.getTotalLength();
        for(const [distance,kind] of [[0,'endpoint'],[length,'endpoint'],[length/2,'midpoint']]){
          const point=element.getPointAtLength(distance);add(point.x,point.y,kind,id);
        }
      }catch{}
    }
  }
  return candidates;
}
function updateSnap(event){
  if(!snapToggle?.checked){hideSnap();return;}
  const point=localPoint(event),screenMatrix=svg.getScreenCTM();if(!screenMatrix){hideSnap();return;}
  const mode=snapMode?.value||'auto';let best=null,bestDistance=Infinity;
  for(const candidate of snapCandidates()){
    if(mode!=='auto'&&candidate.kind!==mode)continue;
    const screenPoint=new DOMPoint(candidate.x,candidate.y).matrixTransform(screenMatrix);
    const distance=Math.hypot(screenPoint.x-event.clientX,screenPoint.y-event.clientY);
    if(distance<bestDistance){bestDistance=distance;best=candidate;}
  }
  if(!best||bestDistance>18){hideSnap();return;}
  const world=new DOMPoint(best.x,best.y).matrixTransform(inverseWorld);activeSnap={...best,world};
  const marker=ensureSnapMarker();marker.setAttribute('transform',`translate(${best.x} ${best.y})`);marker.style.display='';
  if(snapStatus)snapStatus.textContent=`${best.kind==='endpoint'?'端点':best.kind==='midpoint'?'中点':'中心'} · ${best.entityId} · X ${world.x.toFixed(3)} Y ${world.y.toFixed(3)}`;
}
function boundsOf(paths){
  if(!paths.length)return null;
  return paths.reduce((bounds,el)=>{const b=el.getBBox();return [Math.min(bounds[0],b.x),Math.min(bounds[1],b.y),Math.max(bounds[2],b.x+b.width),Math.max(bounds[3],b.y+b.height)];},[Infinity,Infinity,-Infinity,-Infinity]);
}
function fit(paths){
  const bounds=boundsOf(paths);
  if(!bounds){setView(initialView);return;}
  const cx=(bounds[0]+bounds[2])/2,cy=(bounds[1]+bounds[3])/2;
  const rect=svg.getBoundingClientRect(),aspect=rect.width/Math.max(1,rect.height);
  let w=Math.max(bounds[2]-bounds[0],initialView[2]*.001)*1.15;
  let h=Math.max(bounds[3]-bounds[1],initialView[3]*.001)*1.15;
  if(w/h<aspect)w=h*aspect;else h=w/aspect;
  setView([cx-w/2,cy-h/2,w,h]);
}
function applyLayers(){
  allPaths.forEach(el=>{
    const layers=JSON.parse(el.dataset.layers||'[]');
    el.style.display=layers.every(layer=>layerVisibility.get(layer)!==false)?'':'none';
  });
  const visible=new Set(visiblePaths().map(el=>el.dataset.entityId));
  for(const id of selected)if(!visible.has(id))selected.delete(id);
  paint();
}
function captureView(){
  const [x,y,w,h]=viewBox(),a=new DOMPoint(x,y).matrixTransform(inverseWorld),b=new DOMPoint(x+w,y+h).matrixTransform(inverseWorld);
  return {worldView:[a.x,a.y,b.x,b.y],selected:[...selected],hiddenLayers:[...layerVisibility].filter(([,on])=>!on).map(([layer])=>layer)};
}
function restoreView(state){
  if(!state||typeof state!=='object')return;
  if(Array.isArray(state.worldView)&&state.worldView.length===4&&state.worldView.every(Number.isFinite)){
    const [x0,y0,x1,y1]=state.worldView,a=new DOMPoint(x0,y0).matrixTransform(worldMatrix),b=new DOMPoint(x1,y1).matrixTransform(worldMatrix);
    setView([Math.min(a.x,b.x),Math.min(a.y,b.y),Math.abs(a.x-b.x),Math.abs(a.y-b.y)]);
  }
  if(Array.isArray(state.hiddenLayers)){
    const hidden=new Set(state.hiddenLayers.filter(x=>typeof x==='string'));
    layerInputs.forEach(input=>{input.checked=!hidden.has(input.dataset.layer);layerVisibility.set(input.dataset.layer,input.checked);});
  }
  if(Array.isArray(state.selected)){
    const ids=new Set(primitives.map(el=>el.dataset.entityId));selected.clear();
    state.selected.filter(id=>ids.has(id)).forEach(id=>selected.add(id));
  }
  applyLayers();
}
function clearReplay(){allPaths.forEach(el=>el.classList.remove('replay'));operationButtons.forEach(button=>button.classList.remove('active'));}
function clearSelection(){clearReplay();selected.clear();paint();}
function querySelection(arguments_){
  if(readOnly){document.getElementById('queryStatus').textContent='历史图纸只读，请先返回当前文件';return;}
  if(window.parent===window){document.getElementById('queryStatus').textContent='请在工作台中打开图纸后查询';return;}
  if(pending)return;
  pending=++requestSequence;
  const statusNode=document.getElementById('queryStatus');
  statusNode.textContent='正在查询…';
  document.querySelectorAll('button').forEach(button=>button.disabled=true);
  window.parent.postMessage({type:'cad-query',requestId:pending,assetPath,arguments:arguments_},'*');
}
function applyQueryResult(result){
  const ids=Array.isArray(result?.entities)?result.entities.map(item=>item?.ref?.entity_id||item?.id).filter(Boolean):
    (Array.isArray(result?.matched_ids)?result.matched_ids:[]);
  const available=new Set(primitives.map(element=>element.dataset.entityId));
  clearReplay();selected.clear();ids.map(String).filter(id=>available.has(id)).forEach(id=>selected.add(id));paint();
  const visible=visiblePaths().filter(element=>selected.has(element.dataset.entityId));
  if(visible.length)fit(visible);
  const hidden=ids.filter(id=>!available.has(String(id))).length;
  const total=Number.isFinite(Number(result?.total_count))?Number(result.total_count):ids.length;
  document.getElementById('queryStatus').textContent=`查询命中 ${total} 个实体，当前高亮 ${ids.length} 个${hidden?`，其中 ${hidden} 个当前视图不可见`:''}`;
}
function showOperation(index){
  const step=operationHistory[index];if(!step)return;
  clearReplay();
  const ids=new Set(step.ids||[]),paths=visiblePaths().filter(el=>ids.has(el.dataset.entityId));
  paths.forEach(el=>el.classList.add('replay'));
  operationButtons[index]?.classList.add('active');
  const present=new Set(paths.map(el=>el.dataset.entityId)),missing=[...ids].filter(id=>!present.has(id));
  editStatus.textContent=`步骤 ${index+1}/${operationHistory.length}：${step.label} · ${present.size} 个当前可见对象`+(missing.length?` · ${missing.length} 个已删除或当前隐藏对象`:'');
}
function cancelBatchRequest(){if(batchPending!==null){batchSequence++;batchPending=null;}}
function loadBatchStep(index){
  const step=operationHistory[index];
  if(!step?.batch||!step.run_id||window.parent===window){showOperation(index);return;}
  pauseHistory();
  if(!readOnly)currentView=captureView();
  readOnly=true;batchPending=++batchSequence;
  historyStatus.textContent='正在读取批次子步骤 · 只读';editStatus.textContent='批次子步骤只读，请先返回当前文件';refreshControls();
  window.parent.postMessage({type:'cad-batch-step-request',requestId:batchPending,assetPath,runId:step.run_id,stepIndex:Number(step.step_index||0),side:'after'},'*');
}
function replaceScene(scene){
  const parsed=new DOMParser().parseFromString(scene.svg,'image/svg+xml'), replacement=parsed.documentElement;
  if(replacement.localName!=='svg'||parsed.querySelector('parsererror'))throw new Error('历史图纸预览格式错误');
  if(drag){drag.rect?.remove();if(svg.hasPointerCapture(drag.id))svg.releasePointerCapture(drag.id);drag=null;}
  if(gripDrag){if(svg.hasPointerCapture(gripDrag.pointerId))svg.releasePointerCapture(gripDrag.pointerId);gripDrag=null;}
  clearGripPreview();gripLayer=null;
  svg.classList.remove('panning');
  svg.replaceChildren(...[...replacement.childNodes].map(node=>document.importNode(node,true)));
  snapMarker=null;activeSnap=null;ensureSnapMarker();ensureGripLayer();
  for(const name of ['viewBox','data-world-transform','data-initial-viewbox'])svg.setAttribute(name,replacement.getAttribute(name));
  initialView=svg.dataset.initialViewbox.split(/\s+/).map(Number);
  worldMatrix=new DOMMatrix(JSON.parse(svg.dataset.worldTransform));inverseWorld=worldMatrix.inverse();
  primitives=[...svg.querySelectorAll('.entity:not(.pick-stroke)')];allPaths=[...svg.querySelectorAll('.entity')];
  const layerList=document.querySelector('.layer-list');layerList.replaceChildren();
  for(const [name,props] of Object.entries(scene.layers)){
    const label=document.createElement('label'),input=document.createElement('input');
    input.type='checkbox';input.dataset.layer=name;input.checked=props.visible;label.append(input,document.createTextNode(' '+name));layerList.append(label);
  }
  layerInputs=[...layerList.querySelectorAll('input')];layerVisibility=new Map(layerInputs.map(input=>[input.dataset.layer,input.checked]));
  const warnings=document.getElementById('renderWarnings');warnings.replaceChildren();
  if(scene.warnings.length){
    const notice=document.createElement('details'),summary=document.createElement('summary'),list=document.createElement('ul');
    notice.className='render-warning';summary.textContent=`历史预览有 ${scene.warnings.length} 条提示，部分内容可能未显示完整`;
    for(const item of scene.warnings){const li=document.createElement('li');li.textContent=item.message||item.reason||item.characters||item.code;list.append(li);}
    notice.append(summary,list);warnings.append(notice);
  }
  document.getElementById('entityCount').textContent=`${scene.rendered_entities} / ${scene.entity_count} 个模型实体`;
  document.getElementById('coordinates').textContent='';selected.clear();applyLayers();setView(initialView);
}
function cancelHistoryRequest(){
  if(historyPending!==null){
    window.parent.postMessage({type:'cad-history-cancel',assetPath,requestId:historyPending},'*');
    revisionSelect.value=historyIndex<0?'current':String(historyIndex);
  }
  historySequence++;historyPending=null;
  const resolve=historyResolve;historyResolve=null;resolve?.(false);
}
function pauseHistory(){
  playing=false;playSequence++;cancelHistoryRequest();cancelBatchRequest();refreshControls();
}
function loadHistory(index){
  if(!frames[index]||pending!==null)return Promise.resolve(false);
  if(window.parent===window){historyStatus.textContent='请在工作台中打开图纸后查看历史';return Promise.resolve(false);}
  cancelHistoryRequest();
  if(!readOnly)currentView=captureView();
  readOnly=true;historyPending=++historySequence;revisionSelect.value=String(index);
  historyStatus.textContent='正在读取历史快照 · 只读';editStatus.textContent='历史图纸只读，请先返回当前文件';refreshControls();
  const result=new Promise(resolve=>{historyResolve=resolve;});
  window.parent.postMessage({type:'cad-history-request',requestId:historyPending,assetPath,index,...frames[index]},'*');
  return result;
}
function returnCurrent(){
  pauseHistory();
  if(!assetPath||window.parent===window)return;
  historyStatus.textContent='正在重新读取当前文件…';
  window.parent.postMessage({type:'cad-history-current',assetPath,viewerState:currentView},'*');
}
revisionSelect.onchange=()=>{const target=revisionSelect.value;pauseHistory();if(target==='current')returnCurrent();else loadHistory(Number(target));};
document.getElementById('historyPrevious').onclick=()=>{pauseHistory();loadHistory(historyIndex<0?Math.max(0,revisionHistory.cursor-1):historyIndex-1);};
document.getElementById('historyNext').onclick=()=>{pauseHistory();loadHistory(historyIndex<0?revisionHistory.cursor+1:historyIndex+1);};
document.getElementById('historyCurrent').onclick=returnCurrent;
document.getElementById('replay').onclick=async()=>{
  if(playing){pauseHistory();historyStatus.textContent='已暂停 · 历史图纸只读';return;}
  playing=true;const run=++playSequence;refreshControls();
  const start=historyIndex<0||historyIndex===frames.length-1?0:historyIndex+1;
  for(let index=start;index<frames.length;index++){
    if(run!==playSequence)return;
    if(!await loadHistory(index)){if(run===playSequence)pauseHistory();return;}
    await new Promise(resolve=>setTimeout(resolve,700));
  }
  if(run===playSequence){playing=false;refreshControls();}
};
window.addEventListener('message',event=>{
  const data=event.data;
  if(event.source!==window.parent||data?.type!=='cad-batch-step-result'||data.assetPath!==assetPath||data.requestId!==batchPending)return;
  batchPending=null;
  try{
    if(!data.ok)throw new Error(data.error||'读取批次子步骤失败');
    replaceScene(data.scene);
    historyStatus.textContent=`批次步骤 ${Number(data.stepIndex)+1} · ${data.operation||'CAD operation'} · 只读`;
    editStatus.textContent='批次子步骤只读，请先返回当前文件';
  }catch(error){historyStatus.textContent='读取批次子步骤失败：'+error.message+'；可返回当前文件';}
  refreshControls();
});
window.addEventListener('message',event=>{
  const data=event.data;
  if(event.source!==window.parent||data?.type!=='cad-history-result'||data.assetPath!==assetPath||data.requestId!==historyPending)return;
  historyPending=null;const resolve=historyResolve;historyResolve=null;
  let ok=false;
  try{
    if(!data.ok)throw new Error(data.error||'读取历史失败');
    replaceScene(data.scene);historyIndex=data.index;revisionSelect.value=String(historyIndex);
    historyStatus.textContent=`${frames[historyIndex].label} · ${data.snapshot.revision_id} · ${data.snapshot.exists?'历史图纸只读':'文件尚不存在 · 只读'}`;
    ok=true;
  }catch(error){revisionSelect.value=historyIndex<0?'current':String(historyIndex);historyStatus.textContent='读取历史失败：'+error.message+'；可返回当前文件';}
  refreshControls();resolve?.(ok);
});
svg.addEventListener('click',e=>{
  if(suppressClick){suppressClick=false;return;}if(pending)return;
  const item=e.target.closest('.entity'), snapped=activeSnap&&activeSnap.entityId;
  const id=item?.dataset.entityId||snapped;if(!id)return;
  if(selected.has(id))selected.delete(id);else selected.add(id);paint();
  const selectedItem=item||[...svg.querySelectorAll('.entity')].find(element=>element.dataset.entityId===id);
  if(selected.size===1&&['TEXT','MTEXT'].includes(selectedItem?.dataset.entityType))document.getElementById('text').value=selectedItem.textContent;
});
svg.addEventListener('dblclick',e=>{
  const item=e.target.closest('.entity');
  if(item){selected.clear();selected.add(item.dataset.entityId);paint();fit(visiblePaths().filter(el=>selected.has(el.dataset.entityId)));}
});
svg.addEventListener('wheel',e=>{
  e.preventDefault();if(drag)return;
  const p=localPoint(e),[x,y,w,h]=viewBox(),factor=Math.exp(Math.max(-.7,Math.min(.7,e.deltaY*.0015)));
  setView([p.x+(x-p.x)*factor,p.y+(y-p.y)*factor,w*factor,h*factor]);
},{passive:false});
svg.addEventListener('pointerdown',e=>{
  suppressClick=false;
  if(pending||![0,1].includes(e.button))return;
  const grip=e.target.closest?.('.cad-grip');
  if(grip&&e.button===0){beginGripDrag(e,grip);return;}
  const item=e.target.closest('.entity'), snapped=activeSnap&&activeSnap.entityId;
  if(e.button===0&&(item||snapped)&&!e.shiftKey&&!spaceDown)return;
  e.preventDefault();svg.setPointerCapture(e.pointerId);
  const matrix=svg.getScreenCTM().inverse(),start=localPoint(e,matrix);
  drag={mode:e.shiftKey&&e.button===0?'window':'pan',start,matrix,view:viewBox(),client:[e.clientX,e.clientY],id:e.pointerId,append:e.ctrlKey||e.metaKey};
  if(drag.mode==='pan')svg.classList.add('panning');
  else{drag.rect=document.createElementNS('http://www.w3.org/2000/svg','rect');drag.rect.setAttribute('class','selection-window');svg.append(drag.rect);}
});
svg.addEventListener('pointermove',e=>{
  const world=localPoint(e).matrixTransform(inverseWorld);
  updateSnap(e);
  const snappedWorld=activeSnap?.world||world;
  document.getElementById('coordinates').textContent=`X ${snappedWorld.x.toFixed(3)} · Y ${snappedWorld.y.toFixed(3)}${activeSnap?' · 已捕捉':''}`;
  if(gripDrag){
    gripDrag.current=gripPoint(snappedWorld.x,snappedWorld.y,snappedWorld.z);
    gripDrag.patch=updateGripPreview(gripDrag.entity,gripDrag.definition,gripDrag.current)||{};
    return;
  }
  if(!drag)return;
  const p=localPoint(e,drag.matrix);
  if(drag.mode==='pan')setView([drag.view[0]+drag.start.x-p.x,drag.view[1]+drag.start.y-p.y,drag.view[2],drag.view[3]]);
  else{
    drag.box=[Math.min(drag.start.x,p.x),Math.min(drag.start.y,p.y),Math.max(drag.start.x,p.x),Math.max(drag.start.y,p.y)];
    ['x','y','width','height'].forEach((key,i)=>drag.rect.setAttribute(key,[drag.box[0],drag.box[1],drag.box[2]-drag.box[0],drag.box[3]-drag.box[1]][i]));
    drag.crossing=p.x<drag.start.x;
    drag.rect.classList.toggle('crossing',drag.crossing);
  }
});
function finishDrag(e,cancel=false){
  if(!drag)return;
  const distance=Math.hypot(e.clientX-drag.client[0],e.clientY-drag.client[1]);
  if(!cancel&&drag.mode==='window'&&drag.box&&distance>3){
    const grouped=new Map();visiblePaths().forEach(el=>{const id=el.dataset.entityId;if(!grouped.has(id))grouped.set(id,[]);grouped.get(id).push(el);});
    if(!drag.append)selected.clear();
    const [x0,y0,x1,y1]=drag.box;
    for(const [id,paths] of grouped){
      const b=boundsOf(paths);
      const enclosed=b[0]>=x0&&b[1]>=y0&&b[2]<=x1&&b[3]<=y1;
      const crossing=drag.crossing&&b[2]>=x0&&b[0]<=x1&&b[3]>=y0&&b[1]<=y1;
      if(enclosed||crossing)selected.add(id);
    }
    paint();
  }
  if(distance>3)suppressClick=true;
  drag.rect?.remove();svg.classList.remove('panning');
  if(svg.hasPointerCapture(drag.id))svg.releasePointerCapture(drag.id);
  drag=null;
}
svg.addEventListener('pointerup',e=>{if(gripDrag){finishGripDrag(e);return;}finishDrag(e);});
svg.addEventListener('pointercancel',e=>{if(gripDrag){finishGripDrag(e,true);return;}finishDrag(e,true);});
window.addEventListener('keydown',e=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;
  if(e.code==='Space'){spaceDown=true;e.preventDefault();}
  if(e.key==='Escape')clearSelection();
  if(e.key==='0')fit(visiblePaths());
});
window.addEventListener('keyup',e=>{if(e.code==='Space')spaceDown=false;});
window.addEventListener('blur',()=>{spaceDown=false;});
document.querySelector('.layer-list').addEventListener('change',event=>{const input=event.target;if(input.matches('input[data-layer]')){layerVisibility.set(input.dataset.layer,input.checked);applyLayers();}});
snapToggle?.addEventListener('change',()=>{if(!snapToggle.checked)hideSnap();});
snapMode?.addEventListener('change',()=>{activeSnap=null;});
document.getElementById('fit').onclick=()=>fit(visiblePaths());
document.getElementById('fitSelection').onclick=()=>fit(visiblePaths().filter(el=>selected.has(el.dataset.entityId)));
document.getElementById('clear').onclick=clearSelection;
document.getElementById('query').onclick=()=>{
  const input=document.getElementById('queryArgs'), statusNode=document.getElementById('queryStatus');
  try{
    const arguments_=JSON.parse(input.value||'{}');
    if(!arguments_||Array.isArray(arguments_)||typeof arguments_!=='object')throw new Error('查询参数必须是 JSON 对象');
    querySelection(arguments_);
  }catch(error){statusNode.textContent='查询参数错误：'+error.message;}
};
document.getElementById('queryClear').onclick=()=>{document.getElementById('queryArgs').value='{}';clearSelection();document.getElementById('queryStatus').textContent='已清除查询结果';};
ensureSnapMarker();ensureGripLayer();setView(initialView);
document.getElementById('undo').onclick=()=>requestEdit('cad.undo',{});
document.getElementById('redo').onclick=()=>requestEdit('cad.redo',{});
document.getElementById('move').onclick=()=>requestEdit('cad.entity_move',{delta:[Number(document.getElementById('dx').value||0),Number(document.getElementById('dy').value||0),0]});
document.getElementById('rotate').onclick=()=>requestEdit('cad.entity_rotate',{angle:Number(document.getElementById('angle').value||0),center:{x:Number(document.getElementById('cx').value||0),y:Number(document.getElementById('cy').value||0),z:0}});
document.getElementById('delete').onclick=()=>requestEdit('cad.entity_delete',{});
document.getElementById('updateText').onclick=()=>requestEdit('cad.entity_update',{patch:{text:document.getElementById('text').value}});
document.getElementById('saveSelection').onclick=()=>requestEdit('cad.selection_create',{name:document.getElementById('selectionName').value||'picked'});
operationButtons.forEach(button=>button.onclick=()=>{
  const index=Number(button.dataset.historyIndex);
  if(button.dataset.batch==='true')loadBatchStep(index);
  else if(!readOnly)showOperation(index);
});
window.addEventListener('message',event=>{
  if(event.source===window.parent&&event.data?.type==='cad-view-restore'&&event.data.assetPath===assetPath)restoreView(event.data.state);
});
window.addEventListener('message',event=>{
  if(event.source!==window.parent||event.data?.type!=='cad-query-result'||event.data.requestId!==pending)return;
  pending=null;document.querySelectorAll('button').forEach(button=>button.disabled=false);refreshControls();
  if(event.data.ok)applyQueryResult(event.data.result||event.data);
  else document.getElementById('queryStatus').textContent='查询失败：'+(event.data.error||'未知错误');
});
window.addEventListener('message',event=>{if(event.source!==window.parent || event.data?.type!=='cad-operation-result' || event.data.requestId!==pending)return;pending=null;clearGripPreview();document.querySelectorAll('button').forEach(button=>button.disabled=false);if(event.data.history)historyState=event.data.history;refreshControls();if(event.data.deleted){svg.replaceChildren();gripLayer=null;snapMarker=null;selected.clear();paint();}else renderGrips();editStatus.textContent=event.data.ok?(event.data.deleted?'文件创建已撤销，可以重做':'已保存并刷新'):'保存失败：'+(event.data.error||'未知错误');});
if(readOnly){historyStatus.textContent='历史图纸只读';editStatus.textContent='历史图纸只读';}refreshControls();

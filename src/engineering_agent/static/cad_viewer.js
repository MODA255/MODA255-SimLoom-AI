const svg=document.getElementById('cad'), selected=new Set(), status=document.getElementById('selection'), editStatus=document.getElementById('editStatus');
const viewer=document.querySelector('[data-cad-viewer]'), assetPath=viewer?.dataset.assetPath||'';
let pending=null, requestSequence=0, replaySequence=0, drag=null, suppressClick=false, spaceDown=false;
const initialView=svg.dataset.initialViewbox.split(/\s+/).map(Number);
const worldMatrix=new DOMMatrix(JSON.parse(svg.dataset.worldTransform));
const inverseWorld=worldMatrix.inverse();
const primitives=[...svg.querySelectorAll('.entity:not(.pick-stroke)')];
const allPaths=[...svg.querySelectorAll('.entity')];
const layerInputs=[...document.querySelectorAll('input[data-layer]')];
const layerVisibility=new Map(layerInputs.map(input=>[input.dataset.layer,input.checked]));
let historyState={can_undo:!document.getElementById('undo').disabled,can_redo:!document.getElementById('redo').disabled};
function paint(){svg.querySelectorAll('.entity').forEach(e=>e.classList.toggle('selected',selected.has(e.dataset.entityId)));status.textContent=selected.size?`已选择 ${selected.size} 个实体：${[...selected].join(', ')}`:'未选择实体';}
function selectedIds(){return [...selected];}
function requestEdit(operation, arguments_){
  if(pending)return;
  if(window.parent===window){editStatus.textContent='请在工作台中打开文件后编辑';return;}
  if(!['cad.undo','cad.redo'].includes(operation) && !selected.size){editStatus.textContent='请先选择实体';return;}
  if(operation==='cad.entity_update' && selected.size!==1){editStatus.textContent='属性编辑一次只能选择一个实体';return;}
  if(operation==='cad.entity_update'){
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
function clearSelection(){replaySequence++;allPaths.forEach(el=>el.classList.remove('replay'));selected.clear();paint();}
svg.addEventListener('click',e=>{
  if(suppressClick){suppressClick=false;return;}if(pending)return;
  const item=e.target.closest('.entity');if(!item)return;
  const id=item.dataset.entityId;if(selected.has(id))selected.delete(id);else selected.add(id);paint();
  if(selected.size===1&&['TEXT','MTEXT'].includes(item.dataset.entityType))document.getElementById('text').value=item.textContent;
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
  const item=e.target.closest('.entity');
  if(e.button===0&&item&&!e.shiftKey&&!spaceDown)return;
  e.preventDefault();svg.setPointerCapture(e.pointerId);
  const matrix=svg.getScreenCTM().inverse(),start=localPoint(e,matrix);
  drag={mode:e.shiftKey&&e.button===0?'window':'pan',start,matrix,view:viewBox(),client:[e.clientX,e.clientY],id:e.pointerId,append:e.ctrlKey||e.metaKey};
  if(drag.mode==='pan')svg.classList.add('panning');
  else{drag.rect=document.createElementNS('http://www.w3.org/2000/svg','rect');drag.rect.setAttribute('class','selection-window');svg.append(drag.rect);}
});
svg.addEventListener('pointermove',e=>{
  const world=localPoint(e).matrixTransform(inverseWorld);
  document.getElementById('coordinates').textContent=`X ${world.x.toFixed(3)} · Y ${world.y.toFixed(3)}`;
  if(!drag)return;
  const p=localPoint(e,drag.matrix);
  if(drag.mode==='pan')setView([drag.view[0]+drag.start.x-p.x,drag.view[1]+drag.start.y-p.y,drag.view[2],drag.view[3]]);
  else{
    drag.box=[Math.min(drag.start.x,p.x),Math.min(drag.start.y,p.y),Math.max(drag.start.x,p.x),Math.max(drag.start.y,p.y)];
    ['x','y','width','height'].forEach((key,i)=>drag.rect.setAttribute(key,[drag.box[0],drag.box[1],drag.box[2]-drag.box[0],drag.box[3]-drag.box[1]][i]));
  }
});
function finishDrag(e,cancel=false){
  if(!drag)return;
  const distance=Math.hypot(e.clientX-drag.client[0],e.clientY-drag.client[1]);
  if(!cancel&&drag.mode==='window'&&drag.box&&distance>3){
    const grouped=new Map();visiblePaths().forEach(el=>{const id=el.dataset.entityId;if(!grouped.has(id))grouped.set(id,[]);grouped.get(id).push(el);});
    if(!drag.append)selected.clear();
    const [x0,y0,x1,y1]=drag.box;
    for(const [id,paths] of grouped){const b=boundsOf(paths);if(b[0]>=x0&&b[1]>=y0&&b[2]<=x1&&b[3]<=y1)selected.add(id);}
    paint();
  }
  if(distance>3)suppressClick=true;
  drag.rect?.remove();svg.classList.remove('panning');
  if(svg.hasPointerCapture(drag.id))svg.releasePointerCapture(drag.id);
  drag=null;
}
svg.addEventListener('pointerup',e=>finishDrag(e));
svg.addEventListener('pointercancel',e=>finishDrag(e,true));
window.addEventListener('keydown',e=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;
  if(e.code==='Space'){spaceDown=true;e.preventDefault();}
  if(e.key==='Escape')clearSelection();
  if(e.key==='0')fit(visiblePaths());
});
window.addEventListener('keyup',e=>{if(e.code==='Space')spaceDown=false;});
window.addEventListener('blur',()=>{spaceDown=false;});
layerInputs.forEach(input=>input.addEventListener('change',()=>{layerVisibility.set(input.dataset.layer,input.checked);applyLayers();}));
document.getElementById('fit').onclick=()=>fit(visiblePaths());
document.getElementById('fitSelection').onclick=()=>fit(visiblePaths().filter(el=>selected.has(el.dataset.entityId)));
document.getElementById('clear').onclick=clearSelection;
setView(initialView);
document.getElementById('undo').onclick=()=>requestEdit('cad.undo',{});
document.getElementById('redo').onclick=()=>requestEdit('cad.redo',{});
document.getElementById('move').onclick=()=>requestEdit('cad.entity_move',{delta:[Number(document.getElementById('dx').value||0),Number(document.getElementById('dy').value||0),0]});
document.getElementById('rotate').onclick=()=>requestEdit('cad.entity_rotate',{angle:Number(document.getElementById('angle').value||0),center:{x:Number(document.getElementById('cx').value||0),y:Number(document.getElementById('cy').value||0),z:0}});
document.getElementById('delete').onclick=()=>requestEdit('cad.entity_delete',{});
document.getElementById('updateText').onclick=()=>requestEdit('cad.entity_update',{patch:{text:document.getElementById('text').value}});
document.getElementById('saveSelection').onclick=()=>requestEdit('cad.selection_create',{name:document.getElementById('selectionName').value||'picked'});
document.getElementById('replay').onclick=async()=>{
  const run=++replaySequence,paths=visiblePaths(),ids=[...new Set(paths.map(el=>el.dataset.entityId))];
  for(const id of ids){
    if(run!==replaySequence)return;
    const items=paths.filter(el=>el.dataset.entityId===id);items.forEach(el=>el.classList.add('replay'));
    await new Promise(resolve=>setTimeout(resolve,180));items.forEach(el=>el.classList.remove('replay'));
  }
};
window.addEventListener('message',event=>{
  if(event.source===window.parent&&event.data?.type==='cad-view-restore'&&event.data.assetPath===assetPath)restoreView(event.data.state);
});
window.addEventListener('message',event=>{if(event.source!==window.parent || event.data?.type!=='cad-operation-result' || event.data.requestId!==pending)return;pending=null;document.querySelectorAll('button').forEach(button=>button.disabled=false);if(event.data.history)historyState=event.data.history;document.getElementById('undo').disabled=!historyState.can_undo;document.getElementById('redo').disabled=!historyState.can_redo;if(event.data.deleted){svg.replaceChildren();selected.clear();paint();}editStatus.textContent=event.data.ok?(event.data.deleted?'文件创建已撤销，可以重做':'已保存并刷新'):'保存失败：'+(event.data.error||'未知错误');});

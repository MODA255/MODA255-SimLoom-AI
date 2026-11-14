// Geometric bounds and browser picking are checked against analytic fixtures.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {mkdtemp,mkdir}=require('node:fs/promises');
const os=require('node:os'),path=require('node:path'),readline=require('node:readline');
const {chromium}=require('playwright');
const close=(actual,expected,tolerance=.008)=>assert.ok(Math.abs(actual-expected)<tolerance,`${actual} differs from ${expected}`);

async function main(){
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS||os.tmpdir();await mkdir(artifacts,{recursive:true});
  const temp=await mkdtemp(path.join(artifacts,'simloom-native-view-'));
  const server=spawn(process.env.SIMLOOM_PYTHON||'python3',['-u',path.join(__dirname,'cad_server.py'),temp],{cwd:temp,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let browser,stderr='';server.stderr.on('data',chunk=>stderr+=chunk.toString());
  try{
    const lines=readline.createInterface({input:server.stdout});
    const info=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Startup timeout: '+stderr)),30000);
      server.once('error',reject);server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exit ${code}: ${stderr}`));});
      lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
    });
    browser=await chromium.launch({headless:true,...(process.env.SIMLOOM_BROWSER_EXECUTABLE?{executablePath:process.env.SIMLOOM_BROWSER_EXECUTABLE}:{})});
    const page=await browser.newPage({viewport:{width:1500,height:1100}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.url);await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();await page.locator('[data-panel="assets"]').click();
    await page.locator('[data-asset="curves.dxf"]').click();
    const frame=()=>page.frameLocator('#preview iframe');
    await frame().locator('#cad[data-renderer="ezdxf-svg"]').waitFor();
    await page.locator('#preview iframe').scrollIntoViewIfNeeded();
    const ids=info.fixture;
    async function worldBounds(id){
      return frame().locator('#cad').evaluate((svg,id)=>{
        const matrix=new DOMMatrix(JSON.parse(svg.dataset.worldTransform)).inverse();
        const paths=[...svg.querySelectorAll('.entity:not(.pick-stroke)')].filter(e=>e.dataset.entityId===id);
        const points=paths.flatMap(e=>{const b=e.getBBox();return [new DOMPoint(b.x,b.y).matrixTransform(matrix),new DOMPoint(b.x+b.width,b.y+b.height).matrixTransform(matrix)];});
        return [Math.min(...points.map(p=>p.x)),Math.min(...points.map(p=>p.y)),Math.max(...points.map(p=>p.x)),Math.max(...points.map(p=>p.y))];
      },id);
    }
    let bounds=await worldBounds(ids.arc);[0,0,10,10].forEach((v,i)=>close(bounds[i],v));
    bounds=await worldBounds(ids.bulge);close(bounds[0],20);close(bounds[2],40);close(bounds[3]-bounds[1],10);
    bounds=await worldBounds(ids.ellipse);const extent=Math.sqrt(80);[60-extent,-extent,60+extent,extent].forEach((v,i)=>close(bounds[i],v,.02));
    bounds=await worldBounds(ids.insert);[82,0,90,16].forEach((v,i)=>close(bounds[i],v));
    const hole=await frame().locator('#cad').evaluate((svg,id)=>{
      const el=[...svg.querySelectorAll('[data-draw-kind="fill"]')].find(e=>e.dataset.entityId===id);
      const matrix=new DOMMatrix(JSON.parse(svg.dataset.worldTransform));
      return [el.isPointInFill(new DOMPoint(120,10).matrixTransform(matrix)),el.isPointInFill(new DOMPoint(112,2).matrixTransform(matrix))];
    },ids.hatch);
    assert.deepEqual(hole,[false,true],'the hatch hole must remain open both visually and for picking');
    assert.ok(await frame().locator(`[data-entity-id="${ids.dimension}"]:not(.pick-stroke)`).count()>=4);
    assert.ok(await frame().locator(`[data-entity-id="${ids.hidden}"]`).evaluateAll(items=>items.every(e=>getComputedStyle(e).display==='none')));
    await frame().locator('input[data-layer="DETAIL"]').uncheck();
    const nested=await frame().locator(`[data-entity-id="${ids.insert}"]`).evaluateAll(items=>items.map(e=>({visible:getComputedStyle(e).display!=='none',detail:JSON.parse(e.dataset.layers).includes('DETAIL')})));
    assert.ok(nested.some(x=>x.detail&&!x.visible));assert.ok(nested.some(x=>!x.detail&&x.visible));
    await frame().locator('input[data-layer="PART"]').uncheck();
    assert.ok(await frame().locator(`[data-entity-id="${ids.insert}"]`).evaluateAll(items=>items.every(e=>getComputedStyle(e).display==='none')));
    await frame().locator('input[data-layer="PART"]').check();await frame().locator('input[data-layer="DETAIL"]').check();
    async function screen(x,y){
      const offset=await page.locator('#preview iframe').evaluate(el=>{const b=el.getBoundingClientRect();return {x:b.x+el.clientLeft,y:b.y+el.clientTop};});
      const point=await frame().locator('#cad').evaluate((svg,{x,y})=>{
        const p=new DOMPoint(x,y).matrixTransform(new DOMMatrix(JSON.parse(svg.dataset.worldTransform))).matrixTransform(svg.getScreenCTM());return {x:p.x,y:p.y};
      },{x,y});return {x:offset.x+point.x,y:offset.y+point.y};
    }
    const snapEndpoint=await screen(0,0);await page.mouse.move(snapEndpoint.x,snapEndpoint.y);await page.waitForTimeout(40);
    assert.notEqual(await frame().locator('.snap-marker-group').evaluate(el=>getComputedStyle(el).display),'none');
    assert.match(await frame().locator('#snapStatus').textContent(),/端点/);
    await frame().locator('#snapMode').selectOption('center');
    const snapCenter=await screen(5,5);await page.mouse.move(snapCenter.x,snapCenter.y);await page.waitForTimeout(40);
    assert.match(await frame().locator('#snapStatus').textContent(),/中心/);
    await frame().locator('#snap').uncheck();assert.equal(await frame().locator('.snap-marker-group').evaluate(el=>getComputedStyle(el).display),'none');
    await frame().locator('#snap').check();await frame().locator('#snapMode').selectOption('auto');
    let start=await screen(49,11),end=await screen(71,-11);
    await page.keyboard.down('Shift');await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:8});await page.mouse.up();await page.keyboard.up('Shift');
    assert.match(await frame().locator('#selection').textContent(),new RegExp(ids.ellipse));
    assert.deepEqual(await frame().locator('.entity.selected').evaluateAll(items=>[...new Set(items.map(e=>e.dataset.entityId))]),[ids.ellipse]);
    await frame().locator('#clear').click();
    // Right-to-left uses crossing selection, so an object intersecting the
    // window is selected even when its full bounds extend outside it.
    start=await screen(71,11);end=await screen(49,-11);
    await page.keyboard.down('Shift');await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:8});await page.mouse.up();await page.keyboard.up('Shift');
    assert.match(await frame().locator('#selection').textContent(),new RegExp(ids.ellipse));
    await frame().locator('#clear').click();
    const position=await screen(85,0);await page.mouse.click(position.x,position.y);
    assert.match(await frame().locator('#selection').textContent(),new RegExp(ids.insert));
    const viewBefore=await frame().locator('#cad').getAttribute('viewBox');
    await page.mouse.move(position.x,position.y);await page.mouse.wheel(0,-200);
    await page.waitForTimeout(80);
    const zoomed=await frame().locator('#cad').getAttribute('viewBox');assert.notEqual(zoomed,viewBefore);
    await frame().locator('#dx').fill('5');await frame().locator('#dy').fill('0');
    const old=page.frames().find(f=>f.parentFrame());
    const response=page.waitForResponse(r=>r.url().endsWith('/api/cad/operation'));
    await frame().locator('#move').click();const received=await response;assert.equal(received.status(),200,await received.text());
    const edited=await received.json();assert.equal(edited.persisted_validation.valid,true);
    const insert=edited.document.entities.find(e=>e.id===ids.insert);close(insert.insert.x,95);
    for(let i=0;i<100&&!old.isDetached();i++)await page.waitForTimeout(20);assert.ok(old.isDetached());
    await frame().locator('#cad').waitFor();
    await page.waitForTimeout(100);
    const restored=(await frame().locator('#cad').getAttribute('viewBox')).split(' ').map(Number);
    zoomed.split(' ').map(Number).forEach((v,i)=>close(restored[i],v,.001));
    assert.match(await frame().locator('#selection').textContent(),new RegExp(ids.insert));
    bounds=await worldBounds(ids.insert);[87,0,95,16].forEach((v,i)=>close(bounds[i],v));
    await frame().locator('#fit').click();
    // Previous transaction refreshes can move the iframe below the viewport.
    // Re-centre it before sending screen-space mouse coordinates for panning.
    await page.locator('#preview iframe').scrollIntoViewIfNeeded();
    const panStart=await screen(65,0);
    const beforePan=await frame().locator('#cad').getAttribute('viewBox');
    await page.mouse.move(panStart.x,panStart.y);await page.mouse.down({button:'middle'});
    await page.mouse.move(panStart.x+45,panStart.y+20,{steps:6});await page.mouse.up({button:'middle'});
    assert.notEqual(await frame().locator('#cad').getAttribute('viewBox'),beforePan);
    async function cameraBounds(){
      return frame().locator('#cad').evaluate(svg=>{
        const [x,y,w,h]=svg.getAttribute('viewBox').split(' ').map(Number),m=new DOMMatrix(JSON.parse(svg.dataset.worldTransform)).inverse();
        const a=new DOMPoint(x,y).matrixTransform(m),b=new DOMPoint(x+w,y+h).matrixTransform(m);
        return [a.x,a.y,b.x,b.y];
      });
    }
    const cameraBefore=await cameraBounds();
    // Moving the selected block past the drawing bounds changes the renderer's
    // SVG transform. The camera must retain WORLD coordinates across that edit.
    await frame().locator('#dx').fill('500');
    const originalFrame=page.frames().find(f=>f.parentFrame());
    const movedResponse=page.waitForResponse(r=>r.url().endsWith('/api/cad/operation'));
    await frame().locator('#move').click();const moved=await movedResponse;assert.equal(moved.status(),200,await moved.text());
    for(let i=0;i<100&&!originalFrame.isDetached();i++)await page.waitForTimeout(20);
    await frame().locator('#cad').waitFor();await page.waitForTimeout(100);
    const cameraAfter=await cameraBounds();cameraBefore.forEach((v,i)=>close(cameraAfter[i],v,.001));
    // Restore the edit so the retained screenshot still shows the complete fixture.
    const undoResponse=page.waitForResponse(r=>r.url().endsWith('/api/cad/operation'));
    const movedFrame=page.frames().find(f=>f.parentFrame());await frame().locator('#undo').click();
    assert.equal((await undoResponse).status(),200);
    for(let i=0;i<100&&!movedFrame.isDetached();i++)await page.waitForTimeout(20);
    await frame().locator('#fit').click();
    const screenshot=path.join(temp,'native-geometry-viewer.png');await page.screenshot({path:screenshot,fullPage:true});
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,screenshot,project:info.root,checks:['analytic curve bounds','nested block ownership','hatch holes','layer ancestry','enclosed and crossing window selection','zoom and pan','transaction edit','camera restore across changed bounds']}));
  }catch(error){if(browser){const page=browser.contexts().flatMap(c=>c.pages())[0];if(page)await page.screenshot({path:path.join(temp,'viewer-failure.png'),fullPage:true});}throw error;}
  finally{if(browser)await browser.close();if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

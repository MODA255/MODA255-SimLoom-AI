// Real snapshot browsing, read-only controls and late-response regression.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {mkdtemp,mkdir,readFile,readdir}=require('node:fs/promises');
const path=require('node:path'),os=require('node:os'),readline=require('node:readline');
const {chromium}=require('playwright');
// SVG coordinates are quantized; use the same drawing-space tolerance as the native viewer test.
const close=(actual,expected,message)=>assert.ok(Math.abs(actual-expected)<.008,`${message}: ${actual} != ${expected}`);

async function projectBytes(root,relative=''){
  const result={};
  for(const entry of await readdir(path.join(root,relative),{withFileTypes:true})){
    const name=path.join(relative,entry.name);
    if(entry.isDirectory())Object.assign(result,await projectBytes(root,name));
    else result[name]=(await readFile(path.join(root,name))).toString('base64');
  }
  return result;
}
async function main(){
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS||os.tmpdir();await mkdir(artifacts,{recursive:true});
  const temp=await mkdtemp(path.join(artifacts,'simloom-history-browser-'));
  const server=spawn(process.env.SIMLOOM_PYTHON||'python3',['-u',path.join(__dirname,'cad_server.py'),temp],{cwd:temp,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let stderr='',browser,page;server.stderr.on('data',chunk=>stderr+=chunk.toString());
  try{
    const info=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Server startup timeout: '+stderr)),30000);
      server.once('error',reject);server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`));});
      readline.createInterface({input:server.stdout}).once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
    });
    browser=await chromium.launch({headless:true,...(process.env.SIMLOOM_BROWSER_EXECUTABLE?{executablePath:process.env.SIMLOOM_BROWSER_EXECUTABLE}:{})});
    page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const file='车架 test.dxf';
    async function setup(operation,args){
      const response=await page.request.post(info.url+'/api/cad/operation',{data:{operation,arguments:{project_dir:info.root,input_file:file,output_file:file,auto_repair:false,...args}}});
      assert.equal(response.status(),200,await response.text());return response.json();
    }
    const moved=await setup('cad.entity_move',{entity_ids:['e00001'],delta:[4,0,0]});
    const removed=await setup('cad.entity_delete',{entity_ids:['e00002']});
    const original=await projectBytes(info.root);
    let writes=0;page.on('request',request=>{if(request.url().endsWith('/api/cad/operation'))writes++;});
    await page.goto(info.url);await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();await page.locator('[data-panel="assets"]').click();
    await page.locator(`[data-asset="${file}"]`).click();
    const frame=()=>page.frameLocator('#preview iframe');
    const active=()=>page.frames().find(item=>item.parentFrame());
    async function choose(index){
      await frame().locator('#revision').selectOption(String(index));
      await frame().locator('#historyStatus').filter({hasText:index===0?'初始图纸':index===1?moved.run_id:removed.run_id}).waitFor();
    }
    async function worldStart(){
      return frame().locator('[data-entity-id="e00001"]:not(.pick-stroke)').first().evaluate(el=>{
        const box=el.getBBox(),matrix=new DOMMatrix(JSON.parse(document.getElementById('cad').dataset.worldTransform)).inverse();
        return new DOMPoint(box.x,box.y).matrixTransform(matrix).x;
      });
    }
    await frame().locator('#revision').waitFor();
    assert.equal(await frame().locator('[data-entity-id="e00002"]').count(),0);
    await choose(0);close(await worldStart(),0,'initial snapshot has original position');
    assert.ok(await frame().locator('[data-entity-id="e00002"]').count()>0,'deleted circle is visible in its historical geometry');
    assert.ok(await frame().locator('#move').isDisabled());assert.ok(await frame().locator('#undo').isDisabled());
    assert.equal(await page.locator('#drawingTools').isVisible(),false);assert.equal(await page.locator('#downloadAsset').isVisible(),false);
    await frame().locator('input[data-layer="WHEEL"]').uncheck();
    assert.ok(await frame().locator('[data-entity-id="e00002"]').evaluateAll(items=>items.every(el=>getComputedStyle(el).display==='none')));
    await frame().locator('input[data-layer="WHEEL"]').check();
    // Calling handlers directly must be rejected, even if a UI control is bypassed.
    await active().evaluate(file=>{
      requestEdit('cad.entity_delete',{entity_ids:['e00001']});
      window.parent.postMessage({type:'cad-operation',requestId:'forged',assetPath:file,operation:'cad.entity_delete',arguments:{entity_ids:['e00001']}},'*');
    },file);
    await page.evaluate(()=>{exportDrawing('png');changeDrawing(true);});
    await page.waitForTimeout(100);assert.equal(writes,0);
    await frame().locator('#historyNext').click();
    await frame().locator('#historyStatus').filter({hasText:moved.run_id}).waitFor();
    close(await worldStart(),4,'move snapshot has moved geometry');
    await frame().locator('#historyNext').click();
    await frame().locator('#historyStatus').filter({hasText:removed.run_id}).waitFor();
    assert.equal(await frame().locator('[data-entity-id="e00002"]').count(),0);
    await frame().locator('#historyPrevious').click();
    await frame().locator('#historyStatus').filter({hasText:moved.run_id}).waitFor();
    assert.ok(await frame().locator('[data-entity-id="e00002"]').count()>0);
    await choose(2);await frame().locator('#replay').click();
    await frame().locator('#historyStatus').filter({hasText:'初始图纸'}).waitFor();
    await frame().locator('#replay').click();
    const paused=await frame().locator('#revision').inputValue();
    await page.waitForTimeout(900);assert.equal(await frame().locator('#revision').inputValue(),paused);
    assert.match(await frame().locator('#historyStatus').textContent(),/已暂停/);
    await frame().locator('#replay').click();
    await frame().locator('#historyStatus').filter({hasText:removed.run_id}).waitFor();
    assert.equal(await frame().locator('[data-entity-id="e00002"]').count(),0);

    await frame().locator('#historyCurrent').click();
    await frame().locator('[data-read-only="false"]').waitFor();
    assert.equal(await frame().locator('#move').isDisabled(),false);
    assert.equal(await page.locator('#downloadAsset').isVisible(),true);
    assert.equal(await page.locator('#drawingTools').isVisible(),true);
    close(await worldStart(),4,'returning to current restores moved geometry');
    assert.deepEqual(await projectBytes(info.root),original,'browsing must not change any drawing, sidecar, history, cursor or run');

    // Delay one response to test both rapid version selection and file switching.
    async function delayHistory(){
      let release,received;const gate=new Promise(resolve=>release=resolve),ready=new Promise(resolve=>received=resolve);
      let intercepted=false;
      const handler=async route=>{
        if(intercepted){await route.continue();return;}intercepted=true;
        const response=await route.fetch();received();await gate;await route.fulfill({response});
      };
      await page.route('**/api/cad/history-view?*',handler);
      return {ready,release,remove:()=>page.unroute('**/api/cad/history-view?*',handler)};
    }
    let delayed=await delayHistory();
    await frame().locator('#revision').selectOption('0');await delayed.ready;
    await choose(2);delayed.release();await page.waitForTimeout(200);await delayed.remove();
    assert.equal(await frame().locator('#revision').inputValue(),'2');
    assert.equal(await frame().locator('[data-entity-id="e00002"]').count(),0,'late initial response must not replace newer geometry');
    const detailsBeforePause=await page.locator('#assetDetails').textContent();
    delayed=await delayHistory();
    await frame().locator('#replay').click();await delayed.ready;
    await frame().locator('#replay').click();
    delayed.release();await page.waitForTimeout(200);await delayed.remove();
    assert.equal(await frame().locator('#revision').inputValue(),'2','pause cancels an in-flight frame');
    assert.equal(await frame().locator('[data-entity-id="e00002"]').count(),0);
    assert.equal(await page.locator('#assetDetails').textContent(),detailsBeforePause,'paused response must not replace metadata');
    delayed=await delayHistory();
    await frame().locator('#revision').selectOption('0');await delayed.ready;
    await page.locator('[data-asset="other.dxf"]').click();
    await frame().locator('[data-asset-path="other.dxf"]').waitFor();
    delayed.release();await page.waitForTimeout(200);await delayed.remove();
    assert.equal(await frame().locator('[data-read-only="false"]').count(),1);
    assert.equal(await frame().locator('[data-entity-id="e00002"]').count(),0,'late response must not replace another file');
    assert.equal(await page.locator('#downloadAsset').isVisible(),true);

    await page.locator(`[data-asset="${file}"]`).click();
    const failHistory=route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'CAD_HISTORY_SNAPSHOT_CORRUPT'})});
    await page.route('**/api/cad/history-view?*',failHistory);
    await frame().locator('#revision').selectOption('0');
    await frame().locator('#historyStatus').filter({hasText:'读取历史失败'}).waitFor();
    assert.ok(await frame().locator('#move').isDisabled());
    await page.unroute('**/api/cad/history-view?*',failHistory);
    await frame().locator('#historyCurrent').click();await frame().locator('[data-read-only="false"]').waitFor();
    assert.equal(await frame().locator('#move').isDisabled(),false);

    // File creation's before state is genuinely empty and read-only.
    await page.locator('[data-asset="new.dxf"]').click();
    await frame().locator('#revision').selectOption('0');
    await frame().locator('#historyStatus').filter({hasText:'文件尚不存在'}).waitFor();
    assert.equal(await frame().locator('.entity').count(),0);
    await frame().locator('#historyNext').click();
    await frame().locator('#historyStatus').filter({hasText:'历史图纸只读'}).waitFor();
    assert.ok(await frame().locator('.entity').count()>0);
    assert.equal(writes,0);assert.deepEqual(await projectBytes(info.root),original);
    await frame().locator('#historyCurrent').click();await frame().locator('[data-read-only="false"]').waitFor();
    // Normal edits work again after leaving history.
    await active().evaluate(()=>document.querySelector('.entity').dispatchEvent(new MouseEvent('click',{bubbles:true})));
    await frame().locator('#dx').fill('2');
    const edited=page.waitForResponse(response=>response.url().endsWith('/api/cad/operation'));
    await frame().locator('#move').click();assert.equal((await edited).status(),200);assert.equal(writes,1);
    await frame().locator('#revision option[value="2"]').waitFor({state:'attached'});
    close(await worldStart(),2,'editing works after leaving history');
    assert.deepEqual(errors,[]);
    const screenshot=path.join(temp,'cad-history.png');await page.screenshot({path:screenshot,fullPage:true});
    console.log(JSON.stringify({passed:true,writes,screenshot}));
  }catch(error){if(page)await page.screenshot({path:path.join(temp,'cad-history-failure.png'),fullPage:true});console.error(temp);throw error;}
  finally{if(browser)await browser.close();if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

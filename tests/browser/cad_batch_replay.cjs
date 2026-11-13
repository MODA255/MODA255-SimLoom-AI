// Batch operation replay through the real Web Viewer message/API boundary.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {mkdtemp,mkdir}=require('node:fs/promises');
const path=require('node:path'),os=require('node:os'),readline=require('node:readline');
const {chromium}=require('playwright');

async function main(){
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS||os.tmpdir();await mkdir(artifacts,{recursive:true});
  const temp=await mkdtemp(path.join(artifacts,'simloom-batch-replay-browser-'));
  const server=spawn(process.env.SIMLOOM_PYTHON||'python3',['-u',path.join(__dirname,'cad_server.py'),temp],{cwd:temp,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let stderr='',browser,page;server.stderr.on('data',chunk=>stderr+=chunk.toString());
  try{
    const info=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Server startup timeout: '+stderr)),30000);
      server.once('error',reject);server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`));});
      readline.createInterface({input:server.stdout}).once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
    });
    browser=await chromium.launch({headless:true,...(process.env.SIMLOOM_BROWSER_EXECUTABLE?{executablePath:process.env.SIMLOOM_BROWSER_EXECUTABLE}:{})});
    page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const file='车架 test.dxf';
    const response=await page.request.post(info.url+'/api/cad/operation',{data:{operation:'cad.batch_apply',arguments:{project_dir:info.root,input_file:file,output_file:file,auto_repair:false,operations:[
      {operation:'cad.entity_move',entity_ids:['e00001'],delta:[3,0,0]},
      {operation:'cad.entity_delete',entity_ids:['e00002']},
    ]}}});
    assert.equal(response.status(),200,await response.text());
    await page.goto(info.url);await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();await page.locator('[data-panel="assets"]').click();
    await page.locator(`[data-asset="${file}"]`).click();
    const frame=()=>page.frameLocator('#preview iframe');
    await frame().locator('[data-history-index="0"]').waitFor();
    await frame().locator('[data-history-index="0"]').click();
    await frame().locator('#historyStatus').filter({hasText:'批次步骤 1'}).waitFor();
    assert.equal(await frame().locator('[data-read-only="true"]').count(),1);
    await frame().locator('[data-history-index="1"]').click();
    await frame().locator('#historyStatus').filter({hasText:'批次步骤 2'}).waitFor();
    await frame().locator('#historyCurrent').click();
    await frame().locator('[data-read-only="false"]').waitFor();
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true}));
  }catch(error){if(page)await page.screenshot({path:path.join(temp,'batch-replay-failure.png'),fullPage:true});console.error(temp);throw error;}
  finally{if(browser)await browser.close();if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {mkdtemp,mkdir,readFile}=require('node:fs/promises');
const os=require('node:os'),path=require('node:path'),readline=require('node:readline');
const {chromium}=require('playwright');
async function main(){
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS||os.tmpdir();await mkdir(artifacts,{recursive:true});
  const temp=await mkdtemp(path.join(artifacts,'simloom-software-jobs-'));
  const fixture=spawn(process.env.SIMLOOM_PYTHON||'python3',['-u',path.join(__dirname,'software_jobs_server.py'),temp],{env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']});
  let browser,stderr='';fixture.stderr.on('data',data=>stderr+=data);
  try{
    const lines=readline.createInterface({input:fixture.stdout});
    const info=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Fixture startup timeout: '+stderr)),30000);
      fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(new Error(`Fixture exit ${code}: ${stderr}`));});
      lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
    });
    browser=await chromium.launch({headless:true,...(process.env.SIMLOOM_BROWSER_EXECUTABLE?{executablePath:process.env.SIMLOOM_BROWSER_EXECUTABLE}:{})});
    const page=await browser.newPage({viewport:{width:1450,height:1100}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.url);
    await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();
    await page.locator('[data-panel="settings"]').click();
    await page.locator('#refreshWorkflows').click();
    const resume=page.locator(`[data-resume-workflow="${info.workflow_id}"]`);
    await resume.waitFor();
    await page.locator('.workflows-card').screenshot({path:path.join(temp,'workflow-before-resume.png')});
    await resume.click();
    await resume.waitFor({state:'detached'});
    assert.equal(JSON.parse(await readFile(path.join(info.project_dir,'artifacts/workflow-final.json'),'utf8')).fixture,true);
    const task=await (await page.request.get(info.url+'/api/tasks/'+info.workflow_task)).json();
    assert.equal(task.status,'completed');
    await page.locator('.workflows-card').screenshot({path:path.join(temp,'workflow-completed.png')});
    await page.locator('#refreshSoftwareJobs').click();
    const completed=page.locator(`[data-software-job="${info.completed}"]`);
    await completed.locator('[data-software-action="collect"]').waitFor();
    await completed.locator('[data-software-action="logs"]').click();
    await page.waitForFunction(()=>document.querySelector('#softwareJobLogText').textContent.includes('fixture runner finished'));
    await completed.locator('[data-software-action="collect"]').click();
    await page.waitForFunction(id=>document.querySelector(`[data-software-job="${id}"]`).textContent.includes('结果已回收'),info.completed);
    const output=JSON.parse(await readFile(path.join(info.project_dir,'artifacts/completed.json'),'utf8'));
    assert.equal(output.request_id,info.completed);
    const active=page.locator(`[data-software-job="${info.active}"]`);
    await active.locator('[data-software-action="cancel"]').click();
    let status;
    for(let i=0;i<100;i++){
      const response=await page.request.post(info.url+'/api/software-jobs',{data:{tool:'catia',action:'status',request_id:info.active}});
      status=await response.json();if(status.status==='cancelled')break;await new Promise(resolve=>setTimeout(resolve,100));
    }
    assert.equal(status.status,'cancelled');assert.equal(status.requires_review,true);
    await page.locator('#refreshSoftwareJobs').click();
    await active.locator('[data-software-action="release"]').waitFor();
    await page.locator('.software-jobs-card').screenshot({path:path.join(temp,'software-jobs.png')});
    await active.locator('[data-software-action="release"]').click();
    await page.locator('#softwareStopped').check();
    await page.locator('#softwareReleaseReason').fill('Browser fixture runner confirmed stopped');
    await page.locator('#softwareReleaseForm button[type="submit"]').click();
    await page.waitForFunction(()=>document.querySelector('#softwareReleaseForm').hidden);
    await active.locator('[data-software-action="release"]').waitFor({state:'detached'});
    await page.setViewportSize({width:390,height:844});
    await page.locator('.software-jobs-card').scrollIntoViewIfNeeded();
    await page.locator('.software-jobs-card').screenshot({path:path.join(temp,'software-jobs-mobile.png')});
    const overflow=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1&&el.getBoundingClientRect().width>0).slice(0,12).map(el=>({tag:el.tagName,id:el.id,cls:el.className,right:el.getBoundingClientRect().right})));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,JSON.stringify(overflow));
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,artifacts:temp}));
  }finally{
    if(browser)await browser.close();fixture.stdin.end();
    const exited=new Promise(resolve=>fixture.once('exit',resolve));
    const timeout=setTimeout(()=>fixture.kill('SIGTERM'),8000);await exited;clearTimeout(timeout);
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});

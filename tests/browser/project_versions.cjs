// Original file version capture, historical preview, download and export.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {mkdtemp,mkdir,readFile,writeFile}=require('node:fs/promises');
const path=require('node:path'),os=require('node:os'),readline=require('node:readline');
const {chromium}=require('playwright');
async function main(){
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS||os.tmpdir();await mkdir(artifacts,{recursive:true});
  const temp=await mkdtemp(path.join(artifacts,'simloom-versions-browser-'));
  const server=spawn(process.env.SIMLOOM_PYTHON||'python3',['-u',path.join(__dirname,'cad_server.py'),temp],{cwd:temp,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let stderr='',browser,page;server.stderr.on('data',chunk=>stderr+=chunk.toString());
  try{
    const info=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Server startup timeout: '+stderr)),30000);
      server.once('error',reject);server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`));});
      readline.createInterface({input:server.stdout}).once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
    });
    const native=Buffer.from([0,1,128,254,255]),csv='time,force\n'+Array.from({length:60},(_,i)=>`${i},${i*2}\n`).join('');
    await writeFile(path.join(info.root,'part.CATPart'),native);await writeFile(path.join(info.root,'curve.csv'),csv);
    await writeFile(path.join(info.root,'z-broken.json'),'{"unfinished":');
    const curves=await readFile(path.join(info.root,'curves.dxf'));
    browser=await chromium.launch({headless:true,...(process.env.SIMLOOM_BROWSER_EXECUTABLE?{executablePath:process.env.SIMLOOM_BROWSER_EXECUTABLE}:{})});
    page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.url);await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();await page.locator('[data-panel="projects"]').click();
    await page.locator('#versionLabel').fill('保留完整工程输入');
    const saving=page.waitForResponse(response=>response.url().endsWith('/api/version'));
    await page.locator('#createVersion').click();const response=await saving;assert.equal(response.status(),201);
    const snapshot=await response.json();assert.equal(snapshot.storage,'original_files');
    const csvIndex=snapshot.asset_paths.indexOf('curve.csv'),cadIndex=snapshot.asset_paths.indexOf('curves.dxf');
    assert.ok(snapshot.asset_paths.includes('part.CATPart'));
    await writeFile(path.join(info.root,'part.CATPart'),'new native bytes');
    await writeFile(path.join(info.root,'curve.csv'),'time,force\n0,999\n');
    await writeFile(path.join(info.root,'curves.dxf'),await readFile(path.join(info.root,'other.dxf')));
    await page.locator('#versionLabel').fill('调参后版本');
    const savingSecond=page.waitForResponse(response=>response.url().endsWith('/api/version'));
    await page.locator('#createVersion').click();
    const secondResponse=await savingSecond;assert.equal(secondResponse.status(),201);
    const secondSnapshot=await secondResponse.json();
    await page.locator('#versionTimeline .timeline-node').last().click();
    await page.locator('#versionInspectTitle').filter({hasText:'调参后版本'}).waitFor();
    const versionHtmlDownload=page.waitForEvent('download');
    await page.locator('#downloadVersionHtml').click();
    assert.equal((await versionHtmlDownload).suggestedFilename(),'simloom-version-preview.html');
    await page.locator('#closeVersionInspect').click();
    await page.locator('#versionLeft').selectOption(snapshot.id);
    await page.locator('#versionRight').selectOption(secondSnapshot.id);
    const diffResponse=page.waitForResponse(response=>response.url().endsWith('/api/version-visual-diff'));
    await page.locator('#versionDiffButton').click();assert.equal((await diffResponse).status(),200);
    await page.locator('#downloadDiffHtml').waitFor({state:'visible'});
    const diffHtmlDownload=page.waitForEvent('download');
    await page.locator('#downloadDiffHtml').click();
    assert.equal((await diffHtmlDownload).suggestedFilename(),'simloom-version-diff.html');
    await page.locator(`[data-version-view="${snapshot.id}"]`).click();
    await page.locator('#versionStorageNotice').filter({hasText:'原始文件快照'}).waitFor();
    if(csvIndex!==0)await page.locator(`[data-version-asset="${csvIndex}"]`).click();
    await page.frameLocator('#versionInspectPreview iframe').getByText(/60 行/).waitFor();
    await page.locator(`[data-version-asset="${cadIndex}"]`).click();
    const frame=()=>page.frameLocator('#versionInspectPreview iframe');
    await frame().locator('[data-read-only="true"]').waitFor();
    assert.ok(await frame().locator('.entity').count()>10,'historical native curves remain, not the single-line replacement');
    assert.ok(await frame().locator('#move').isDisabled());assert.ok(await frame().locator('#undo').isDisabled());
    await page.locator(`[data-version-asset="${snapshot.asset_paths.indexOf('z-broken.json')}"]`).click();
    await page.locator('#versionStorageNotice').filter({hasText:'预览不可用'}).waitFor();
    assert.equal(await page.locator('#versionExportControls').isVisible(),true);
    const downloadEvent=page.waitForEvent('download');
    await page.locator('#versionFiles a').filter({hasText:'下载 part.CATPart'}).click();
    const download=await downloadEvent;
    assert.equal(download.suggestedFilename(),'part.CATPart');assert.deepEqual(await readFile(await download.path()),native);
    await page.locator('#versionExportDir').fill('exports/versions/for-software');
    const exporting=page.waitForResponse(response=>response.url().endsWith('/api/version-export'));
    await page.locator('#exportVersion').click();assert.equal((await exporting).status(),201);
    await page.locator('#versionExportStatus').filter({hasText:'已导出并校验'}).waitFor();
    assert.deepEqual(await readFile(path.join(info.root,'exports/versions/for-software/part.CATPart')),native);
    assert.deepEqual(await readFile(path.join(info.root,'exports/versions/for-software/curves.dxf')),curves);
    assert.equal(await readFile(path.join(info.root,'exports/versions/for-software/curve.csv'),'utf8'),csv);
    assert.equal(await readFile(path.join(info.root,'part.CATPart'),'utf8'),'new native bytes');
    await page.locator('#exportVersion').click();
    await page.locator('#versionExportStatus').filter({hasText:'目标文件夹已存在'}).waitFor();

    // An old semantic-only snapshot must not advertise originals or export.
    await writeFile(path.join(info.root,'.engineering-agent/versions/legacy.json'),JSON.stringify({id:'legacy',label:'旧版摘要',parent:null,created_at:'2026-01-01T00:00:00Z',objects:snapshot.objects}));
    await page.locator('#refreshVersions').click();
    await page.locator('[data-version-view="legacy"]').click();
    await page.locator('#versionStorageNotice').filter({hasText:'旧版仅保存解析摘要'}).waitFor();
    assert.equal(await page.locator('#versionFiles a').count(),0);assert.equal(await page.locator('#versionExportControls').isVisible(),false);
    // A late response from one version must not replace a newer choice.
    let release,received;const gate=new Promise(resolve=>release=resolve),ready=new Promise(resolve=>received=resolve);
    let intercepted=false;
    const handler=async route=>{
      if(intercepted){await route.continue();return;}intercepted=true;
      const fetched=await route.fetch();received();await gate;await route.fulfill({response:fetched});
    };
    await page.route('**/api/version-view?*',handler);
    await page.locator(`[data-version-view="${snapshot.id}"]`).click();await ready;
    await page.locator('[data-version-view="legacy"]').click();
    await page.locator('#versionStorageNotice').filter({hasText:'旧版仅保存解析摘要'}).waitFor();
    release();await page.waitForTimeout(200);await page.unroute('**/api/version-view?*',handler);
    assert.equal(await page.locator('#versionInspectTitle').textContent(),'旧版摘要');
    assert.equal(await page.locator('#versionExportControls').isVisible(),false);
    assert.deepEqual(errors,[]);
    await page.locator(`[data-version-view="${snapshot.id}"]`).click();
    await page.locator('#versionStorageNotice').filter({hasText:'原始文件快照'}).waitFor();
    const screenshot=path.join(temp,'project-version.png');await page.screenshot({path:screenshot,fullPage:true});
    console.log(JSON.stringify({passed:true,originalFiles:snapshot.files.filter(item=>item.exists).length,screenshot}));
  }catch(error){if(page)await page.screenshot({path:path.join(temp,'version-failure.png'),fullPage:true});console.error(temp);throw error;}
  finally{if(browser)await browser.close();if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

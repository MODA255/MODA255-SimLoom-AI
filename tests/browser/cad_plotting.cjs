// End-to-end sheet preparation, title editing, real plotting and downloads.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {mkdtemp, readFile, mkdir} = require('node:fs/promises');
const {createHash} = require('node:crypto');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('playwright');

async function main() {
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS || os.tmpdir();
  await mkdir(artifacts,{recursive:true});
  const temp=await mkdtemp(path.join(artifacts,'simloom-plot-browser-'));
  const server=spawn(process.env.SIMLOOM_PYTHON || 'python3',['-u',path.join(__dirname,'cad_server.py'),temp],
    {cwd:temp,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let browser,stderr=''; server.stderr.on('data',chunk=>stderr+=chunk.toString());
  try {
    const lines=readline.createInterface({input:server.stdout});
    const info=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Startup timeout: '+stderr)),30000);
      server.once('error',reject);
      server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`));});
      lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
    });
    browser=await chromium.launch({headless:true,...(process.env.SIMLOOM_BROWSER_EXECUTABLE?{executablePath:process.env.SIMLOOM_BROWSER_EXECUTABLE}:{})});
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.url);
    await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();
    await page.locator('[data-panel="assets"]').click();
    const file='车架 test.dxf';
    await page.locator(`[data-asset="${file}"]`).click();
    await page.locator('#drawingTools').waitFor({state:'visible'});
    await page.locator('#drawingTools summary').click();
    await page.locator('#titleText').fill('Mounting bracket');
    await page.locator('#titleNumber').fill('SL-101');
    await page.locator('#titleRevision').fill('A');
    await page.locator('#plotPaper').selectOption('A3');
    async function execute(selector) {
      const response=page.waitForResponse(r=>r.url().endsWith('/api/cad/operation'));
      await page.locator(selector).click();
      const received=await response;
      assert.equal(received.status(),200,await received.text());
      const result=await received.json();
      await page.waitForFunction(()=>!document.querySelector('#plotPdf').disabled);
      return result;
    }
    const prepared=await execute('#prepareSheet');
    assert.equal(prepared.persisted_validation.valid,true);
    assert.equal(await page.locator('#plotLayout').inputValue(),'A3');
    assert.equal(await page.locator('#plotPaper').isDisabled(),true);
    await page.locator('#titleRevision').fill('B');
    const updated=await execute('#saveTitle');
    assert.equal(updated.document.sheets.A3.title_block.fields.REV,'B');
    assert.equal(updated.document.entities.filter(e=>e.title_block_id).length,1);
    const before=await readFile(path.join(info.root,file));
    const preview=await execute('#plotPreview');
    assert.equal(preview.validation.valid,true);
    assert.equal(preview.report.page_mm.width,420);
    assert.equal(preview.report.page_mm.height,297);
    assert.equal(preview.report.warnings.length,0);
    await page.locator('#plotResult img').waitFor();
    await page.waitForFunction(()=>{const img=document.querySelector('#plotResult img');return img?.complete&&img.naturalWidth>2000;});
    const imageUrl=await page.locator('#plotResult img').getAttribute('src');
    const pngResponse=await page.request.get(new URL(imageUrl,info.url).href);
    const png=await pngResponse.body();
    assert.equal(pngResponse.headers()['content-type'],'image/png');
    assert.equal(png.readUInt32BE(16),2480);assert.equal(png.readUInt32BE(20),1753);
    const screenshot=path.join(temp,'drawing-print-preview.png');
    await page.screenshot({path:screenshot,fullPage:true});
    const pdf=await execute('#plotPdf');
    assert.equal(pdf.validation.valid,true);
    const download=await page.request.get(new URL(await page.locator('#plotResult a').first().getAttribute('href'),info.url).href);
    assert.match(download.headers()['content-type'],/application\/pdf/);
    const bytes=await download.body();
    assert.equal(bytes.subarray(0,5).toString(),'%PDF-');
    assert.equal(createHash('sha256').update(bytes).digest('hex'),pdf.report.output_sha256);
    const report=await page.request.get(new URL(await page.locator('#plotResult a').nth(1).getAttribute('href'),info.url).href);
    assert.equal((await report.json()).source_sha256,createHash('sha256').update(before).digest('hex'));
    assert.deepEqual(await readFile(path.join(info.root,file)),before,'export must not rewrite source DXF');
    const pngExport=await execute('#plotPng');assert.equal(pngExport.validation.valid,true);
    await page.reload();
    await page.locator('[data-panel="assets"]').click();
    await page.locator(`[data-asset="${file}"]`).click();
    await page.locator('#drawingTools summary').click();
    await page.locator('#plotLayout').selectOption('A3');
    assert.equal(await page.locator('#titleRevision').inputValue(),'B');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,screenshot,pdf:pdf.path,png:preview.path,project:info.root}));
  } catch(error) {
    if(browser){const page=browser.contexts().flatMap(c=>c.pages())[0];if(page)await page.screenshot({path:path.join(temp,'plot-failure.png'),fullPage:true});}
    throw error;
  } finally {
    if(browser)await browser.close();
    if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});

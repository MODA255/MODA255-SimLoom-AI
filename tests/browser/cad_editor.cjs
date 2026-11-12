// Run with: NODE_PATH=<playwright installation>/node_modules node tests/browser/cad_editor.cjs
// SIMLOOM_PYTHON and SIMLOOM_BROWSER_EXECUTABLE can select existing local runtimes.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {mkdtemp, readFile, mkdir} = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('playwright');

async function main() {
  const artifacts=process.env.SIMLOOM_BROWSER_ARTIFACTS || os.tmpdir();
  await mkdir(artifacts,{recursive:true});
  const temp = await mkdtemp(path.join(artifacts, 'simloom-cad-browser-'));
  const server = spawn(process.env.SIMLOOM_PYTHON || 'python3', ['-u', path.join(__dirname, 'cad_server.py'), temp],
    {cwd: temp, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}});
  let browser, stderr = '';
  server.stderr.on('data', chunk => stderr += chunk.toString());
  try {
    const lines = readline.createInterface({input: server.stdout});
    const serverReady = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Server startup timeout: ' + stderr)), 30000);
      server.once('error', reject);
      server.once('exit', code => {clearTimeout(timer); reject(new Error(`Server exited ${code}: ${stderr}`));});
      lines.once('line', line => {clearTimeout(timer); resolve(JSON.parse(line));});
    });
    const info = await serverReady;
    browser = await chromium.launch({headless: true,
      ...(process.env.SIMLOOM_BROWSER_EXECUTABLE ? {executablePath: process.env.SIMLOOM_BROWSER_EXECUTABLE} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let writes = 0;
    page.on('request', request => {if (request.url().endsWith('/api/cad/operation')) writes++;});
    await page.goto(info.url);
    await page.locator('[data-panel="projects"]').click();
    await page.locator(`[data-project="${info.project}"]`).click();
    await page.locator('[data-panel="assets"]').click();
    const file = '车架 test.dxf';
    await page.locator(`[data-asset="${file}"]`).click();
    const frame = () => page.frameLocator('#preview iframe');
    const query = new URLSearchParams({root: info.root, path: file});
    async function document() {
      const response = await page.request.get(info.url + '/api/asset-view?' + query);
      assert.equal(response.status(), 200);
      return (await response.json()).asset.cad_document;
    }
    async function pick(id) {
      await frame().locator('#clear').click();
      await page.locator('#preview iframe').scrollIntoViewIfNeeded();
      const offset = await page.locator('#preview iframe').evaluate(el => {
        const r=el.getBoundingClientRect(); return {x:r.x+el.clientLeft,y:r.y+el.clientTop};
      });
      const point = await frame().locator(`[data-entity-id="${id}"]`).first().evaluate(el => {
        // Click an actual point on the visible stroke. SVG line boxes can have
        // zero height and a circle's center is deliberately unpainted.
        if(typeof el.getPointAtLength==='function'){
          const p=el.getPointAtLength(el.getTotalLength()*.25);
          const q=new DOMPoint(p.x,p.y).matrixTransform(el.getScreenCTM());
          return {x:q.x,y:q.y};
        }
        const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};
      });
      await page.mouse.click(offset.x+point.x,offset.y+point.y);
      assert.ok((await frame().locator('#selection').textContent()).includes(id));
    }
    async function edit(button) {
      const oldFrame = page.frames().find(item => item.parentFrame());
      const response = page.waitForResponse(response => response.url().endsWith('/api/cad/operation'));
      await frame().locator(button).click();
      const result = await response;
      assert.equal(result.status(), 200, await result.text());
      const payload = await result.json();
      assert.equal(payload.persisted_validation.valid, true);
      await page.waitForFunction(() => document.querySelector('#preview iframe') !== null);
      for (let i = 0; i < 100 && !oldFrame.isDetached(); i++) await page.waitForTimeout(20);
      assert.ok(oldFrame.isDetached(), 'the edited view must refresh');
      await frame().locator('[data-cad-viewer]').waitFor();
      return payload;
    }

    await frame().locator('#move').waitFor({state: 'visible'});
    await frame().locator('input[data-layer="FRAME"]').uncheck();
    assert.ok(await frame().locator('[data-entity-id="e00001"]').evaluateAll(items=>items.every(el=>getComputedStyle(el).display==='none')));
    await frame().locator('input[data-layer="FRAME"]').check();
    await pick('e00001');
    await frame().locator('#dx').fill('3');
    await frame().locator('#dy').fill('4');
    await edit('#move');
    let drawing = await document();
    assert.deepEqual(drawing.entities[0].start, {x: 3, y: 4, z: 0});
    assert.deepEqual(drawing.entities[0].end, {x: 13, y: 4, z: 0});

    await edit('#undo');
    assert.equal((await document()).entities[0].start.x,0);
    await edit('#redo');
    assert.equal((await document()).entities[0].start.x,3);

    await pick('e00001');
    await frame().locator('#angle').fill('90');
    await edit('#rotate');
    drawing = await document();
    assert.ok(Math.abs(drawing.entities[0].start.x + 4) < 1e-8);
    assert.ok(Math.abs(drawing.entities[0].start.y - 3) < 1e-8);

    await pick('e00002');
    const beforeInvalid = writes;
    await frame().locator('#updateText').click();
    assert.match(await frame().locator('#editStatus').textContent(), /请选择 TEXT/);
    assert.equal(writes, beforeInvalid, 'text editing a circle must not write');
    await frame().locator('#selectionName').fill('wheel');
    await edit('#saveSelection');
    assert.deepEqual((await document()).selection_sets.wheel.refs, ['e00002']);

    await pick('e00003');
    await frame().locator('#text').fill('<Bracket B> ⌀12');
    await edit('#updateText');
    assert.equal((await document()).entities[2].text, '<Bracket B> ⌀12');

    const beforeForged = writes;
    await page.evaluate(assetPath => window.postMessage({type: 'cad-operation', assetPath,
      operation: 'cad.entity_delete', arguments: {entity_ids: ['e00001']}}, '*'), file);
    await page.waitForTimeout(100);
    assert.equal(writes, beforeForged, 'messages outside the active preview are ignored');

    await pick('e00002');
    await edit('#delete');
    drawing = await document();
    assert.equal(drawing.entities.length, 2);
    assert.deepEqual(drawing.selection_sets.wheel.refs, []);
    await page.reload();
    await page.locator('[data-panel="assets"]').click();
    await page.locator(`[data-asset="${file}"]`).click();
    await frame().locator('#move').waitFor({state: 'visible'});
    assert.equal(await frame().locator('[data-entity-id="e00003"]').textContent(), '<Bracket B> ⌀12');
    await edit('#undo');
    assert.equal((await document()).entities.length,3);
    await edit('#redo');
    assert.equal((await document()).entities.length,2);
    const disk = await readFile(path.join(info.root, file), 'utf8');
    assert.ok(disk.includes('<Bracket B> ⌀12'));
    await page.locator('[data-asset="new.dxf"]').click();
    await frame().locator('#undo').waitFor({state:'visible'});
    const deletion=page.waitForResponse(response=>response.url().endsWith('/api/cad/operation'));
    await frame().locator('#undo').click();
    const removed=await deletion;
    assert.equal(removed.status(),200);
    assert.equal((await removed.json()).document,null);
    await page.locator('[data-asset="new.dxf"]').waitFor({state:'detached'});
    assert.equal(await page.locator('#downloadAsset').isVisible(),false);
    await edit('#redo');
    await page.locator('[data-asset="new.dxf"]').waitFor();
    assert.equal(await page.locator('#downloadAsset').isVisible(),true);
    assert.ok((await readFile(path.join(info.root,'new.dxf'),'utf8')).includes('LINE'));
    await page.locator(`[data-asset="${file}"]`).click();
    await frame().locator('#move').waitFor({state:'visible'});
    assert.deepEqual(errors, []);
    const screenshot = path.join(temp, 'cad-editor.png');
    await page.screenshot({path: screenshot, fullPage: true});
    console.log(JSON.stringify({passed: true, writes, project: info.root, screenshot}));
  } catch(error) {
    if(browser){const pages=browser.contexts().flatMap(context=>context.pages());if(pages[0]){const screenshot=path.join(temp,'cad-editor-failure.png');await pages[0].screenshot({path:screenshot,fullPage:true});console.error(screenshot);}}
    throw error;
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) {server.kill('SIGTERM'); await once(server, 'exit');}
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});

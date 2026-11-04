// Optional real-browser verification. Set PLAYWRIGHT_MODULE to an installed package path.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const [file, screenshot, summary, attackFile, recoveryRunId] = process.argv.slice(2);
  assert(file && screenshot && summary, 'Provide HTML, screenshot, summary output paths');
  const browser = await chromium.launch({executablePath: process.env.DASHBOARD_CHROME || undefined, headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[], requests=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    page.on('request',r=>{if(!r.url().startsWith('file:'))requests.push(r.url());});
    await page.goto(pathToFileURL(file).href);
    await page.locator('.run-button').first().waitFor();
    const total = await page.locator('.run-button').count();
    assert(total>=4,'Need success/rejection/rollback evidence');
    await page.getByRole('button',{name:'逐轮差异',exact:true}).click();
    await page.getByRole('button',{name:'逐行',exact:true}).click();
    await page.getByRole('button',{name:'双层审批',exact:true}).click();
    assert(await page.locator('.hash').count()>0);
    await page.getByRole('button',{name:'审计与回退',exact:true}).click();
    assert(await page.locator('.timeline li').count()>0);
    await page.locator('#filter').selectOption('rolled_back');
    assert(await page.locator('.run-button').count()>0);
    await page.locator('#filter').selectOption('all');
    await page.locator('#search').fill('no-match-expected');
    assert.equal(await page.locator('.run-button').count(),0);
    await page.locator('#search').fill('');
    await page.locator('#filter').selectOption('discarded');
    await page.getByRole('button',{name:'逐轮差异',exact:true}).click();
    assert(await page.locator('.d2h-file-wrapper').count()>0,'Open-source diff renderer must be used');
    await page.locator('#filter').selectOption('all');
    await page.getByRole('button',{name:'测试与过程',exact:true}).click();
    if(recoveryRunId){
      await page.locator('#search').fill(recoveryRunId);
      assert.equal(await page.locator('.run-button').count(),1);
      assert.equal(await page.getByText('发布：准备已中止',{exact:true}).count(),1);
      assert.equal(await page.getByText('准备未完成或已中止；已记录的重测结果不能用于发布，重新准备须重跑全部测试。',{exact:true}).count(),1);
      await page.getByRole('button',{name:'双层审批',exact:true}).click();
      assert.equal(await page.getByText('快照指纹不一致',{exact:true}).count(),0,'Unready review is not a tampered review');
      await page.getByRole('button',{name:'审计与回退',exact:true}).click();
      assert((await page.locator('.tab-panel').innerText()).includes('integration_preparation'));
      assert((await page.locator('.tab-panel').innerText()).includes('aborted'));
      await page.getByRole('button',{name:'测试与过程',exact:true}).click();
    }
    await page.screenshot({path:screenshot,fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'Mobile layout overflows');
    if(attackFile){
      await page.goto(pathToFileURL(attackFile).href);
      await page.locator('.run-button').first().waitFor();
      assert.equal(await page.evaluate(()=>window.injected),undefined);
      assert(await page.locator('h2').innerText().then(t=>t.includes('<img')));
      await page.getByRole('button',{name:'逐轮差异',exact:true}).click();
      assert.equal(await page.locator('.diff-view img, .diff-view script').count(),0);
      assert.equal(await page.evaluate(()=>window.injected),undefined);
      await page.getByRole('button',{name:'双层审批',exact:true}).click();
      assert(await page.getByText('快照指纹不一致',{exact:true}).count()>0);
    }
    assert.deepEqual(errors,[]);
    assert.deepEqual(requests,[]);
    await fs.writeFile(summary,JSON.stringify({passed:true,runs:total,browser:'Chromium',checks:['run selection','tabs','diff2html rendering','approval hashes','audit timeline','status filter','search and empty state','390px responsive layout','zero external requests','zero console/page errors',...(attackFile?['HTML/JSON/diff injection stays inert','changed plan hash flagged']:[]),...(recoveryRunId?['aborted preparation warning','partial verification is not approval','recovery details visible']:[])],requests,errors},null,2)+'\n');
    console.log('Dashboard browser checks passed: '+total+' runs');
  }finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

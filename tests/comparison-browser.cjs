// Optional Chromium validation of the stage-six evidence, with no remote calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const [html, summaryFile, output] = process.argv.slice(2);
  const summary = JSON.parse(await fs.readFile(summaryFile, 'utf8'));
  const selected = (name) => summary.cells.find(c => c.case === name && c.arm === 'guarded_two');
  const id = row => row.run_dir.split(/[\\/]/).at(-1);
  const browser = await chromium.launch({executablePath:process.env.DASHBOARD_CHROME, headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [], external = [];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('console', m => {if (m.type() === 'error') errors.push(m.text());});
    page.on('request', r => {if (!r.url().startsWith('file:')) external.push(r.url());});
    await page.goto(pathToFileURL(html).href);
    await page.locator('.run-button').first().waitFor();
    await page.locator('#search').fill(id(selected('switch')));
    assert.equal(await page.locator('.run-button').count(), 1);
    await page.locator('.run-button').click();
    assert.equal(await page.locator('.round').count(), 2);
    const rounds = await page.locator('.round').allInnerTexts();
    assert(rounds[0].includes('测试失败') && rounds[1].includes('测试通过'));
    await page.getByRole('button',{name:'逐轮差异',exact:true}).click();
    assert.equal(await page.locator('.d2h-file-wrapper').count(), 2);
    await page.locator('#search').fill(id(selected('shipping')));
    await page.locator('.run-button').click();
    assert.equal(await page.getByText('发布：已发布分支',{exact:true}).count(), 1);
    await page.getByRole('button',{name:'双层审批',exact:true}).click();
    assert((await page.locator('.tab-panel').innerText()).includes(selected('shipping').showcase_integration.review_sha256));
    await page.getByRole('button',{name:'审计与回退',exact:true}).click();
    assert((await page.locator('.tab-panel').innerText()).includes(selected('shipping').showcase_integration.commit));
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    await fs.writeFile(output, JSON.stringify({passed:true,checks:['switch actual two rounds fail then pass','two actual diffs','shipping published record','exact review hash visible','published commit visible'],errors,external},null,2)+'\n');
  } finally {await browser.close();}
})().catch(e => {console.error(e);process.exitCode=1;});

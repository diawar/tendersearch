const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Dmitriy Small/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync('tests/fixtures/deal-snapshot.html','utf8');
 await page.route('**/*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({cities:[],cargo_types:[],locations:[]})}));
 await page.setContent('<html><body><div id="fixture"></div></body></html>');
 await page.evaluate(html=>{const doc=new DOMParser().parseFromString(html,'text/html');for(const id of ['deal-estimate','preparation-form']){const node=doc.getElementById(id);if(node)document.getElementById('fixture').append(node);}},html);
 await page.addScriptTag({content:fs.readFileSync('src/tender_search/core/static/core/deal-estimate.js','utf8')});
 assert.equal(await page.inputValue('[name=product_name] >> nth=0'),'Private monitor');
 assert.equal(await page.locator('#deal-estimate [name=product_name]').isDisabled(),true);
 assert.equal(await page.locator('#deal-estimate [name=purchase_unit_gross]').isDisabled(),true);
 assert.equal(await page.locator('#deal-estimate [name=unit_gross]').isDisabled(),true);
 assert.equal(await page.locator('#deal-estimate [name=delivery_gross]').isDisabled(),false);
 assert.match(await page.textContent('#deal-estimate-result'),/80,00/);
 assert.match(await page.textContent('#deal-estimate-status'),/сохранён/);
 assert.deepEqual(errors,[]);console.log('Saved deal restored; commercial fields locked at contract, delivery editable.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

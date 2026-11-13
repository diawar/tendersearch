const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Dmitriy Small/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const script=fs.readFileSync('src/tender_search/core/static/core/private-pages.js','utf8');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext();let current='account-a',offline=false,hold=null,calls=0;
  await context.route('http://privacy.test/**',async route=>{
   if(route.request().url().endsWith('/account/session/')){
    calls++;const scope=current,pending=hold;if(pending)await pending;
    if(offline)return route.abort();return route.fulfill({json:{authenticated:!!scope,scope}});
   }
   return route.fulfill({contentType:'text/html',body:`<html><head><meta charset="utf-8"><script>${script}</script></head><body data-account-scope="${current}"><input value="private-${current}"><button data-copy-text="17929324-1">Копировать<span class="copy-feedback"></span></button><div id="private">private-${current}</div></body></html>`});
  });
  const a=await context.newPage();const unlocked=page=>page.waitForFunction(()=>!document.documentElement.classList.contains('account-locked'));
  let release;hold=new Promise(resolve=>release=resolve);
  await a.goto('http://privacy.test/a');await a.waitForFunction(()=>document.getElementById('account-gate'));
  await a.evaluate(()=>{for(let i=0;i<20;i++)window.dispatchEvent(new Event('focus'));});
  assert.equal(calls,1,'one request despite focus burst');hold=null;release();await unlocked(a);
  const before=calls;await a.evaluate(()=>{for(let i=0;i<20;i++)window.dispatchEvent(new Event('focus'));});
  assert.equal(calls,before,'verified focus is throttled');
  // Clipboard permission/focus changes must not blank an actively edited page.
  await a.addScriptTag({content:fs.readFileSync('src/tender_search/core/static/core/copy.js','utf8')});
  await a.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied=text;window.dispatchEvent(new Event('focus'));}}}));
  await a.locator('input').fill('unsaved note');offline=true;
  await a.evaluate(()=>{const now=Date.now;Date.now=()=>now()+31000;});
  await a.locator('[data-copy-text]').click();assert.equal(await a.evaluate(()=>window.copied),'17929324-1');
  await a.locator('#account-connection').waitFor({state:'visible'});await unlocked(a);
  assert.equal(await a.locator('input').inputValue(),'unsaved note');
  assert.equal(await a.locator('#private').isVisible(),true);
  const failed=calls;await a.evaluate(()=>window.dispatchEvent(new Event('focus')));assert.equal(calls,failed,'failed attempts do not flood server');
  offline=false;await a.evaluate(()=>window.dispatchEvent(new Event('online')));await a.locator('#account-connection').waitFor({state:'hidden'});
  // Hidden/restored tabs still fail closed until verified, including offline.
  await a.evaluate(()=>{window.testHidden=true;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden});document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await a.locator('#private').isVisible(),false);
  offline=true;await a.evaluate(()=>{window.testHidden=false;document.dispatchEvent(new Event('visibilitychange'));});
  await a.waitForFunction(()=>document.getElementById('account-gate').textContent.includes('Не удалось'),null,{polling:100});
  assert.equal(await a.locator('#private').isVisible(),false);
  offline=false;await a.evaluate(()=>window.dispatchEvent(new Event('online')));await unlocked(a);
  // The account change must hide old data immediately, without waiting for fetch.
  current='account-b';offline=true;
  const b=await context.newPage();await b.goto('http://privacy.test/b');
  await a.waitForFunction(()=>document.body.dataset.accountScope==='account-b');
  assert.equal(await a.locator('#private').isVisible(),false);
  assert.equal(await a.locator('#private').textContent(),'private-account-b');
  offline=false;await a.evaluate(()=>window.dispatchEvent(new Event('online')));await unlocked(a);
  hold=new Promise(resolve=>release=resolve);
  await a.evaluate(()=>{const now=Date.now;Date.now=()=>now()+31000;window.dispatchEvent(new Event('focus'));});
  await a.waitForTimeout(50);current='account-c';
  await a.evaluate(()=>window.dispatchEvent(new StorageEvent('storage',{key:'app-account-scope',newValue:'account-c'})));
  hold=null;release();await a.waitForFunction(()=>document.body.dataset.accountScope==='account-c');await unlocked(a);
  assert.equal(await a.locator('#private').textContent(),'private-account-c');
  const base=fs.readFileSync('src/tender_search/core/templates/core/base.html','utf8');
  assert.match(base,/hx-history="false"/);assert.match(base,/"historyCacheSize":0/);
  console.log('PASS: focus coalescing/throttle, offline editing preserved, hidden tabs protected, two-account change hidden immediately, stale responses rejected');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

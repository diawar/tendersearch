const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Dmitriy Small/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const script=fs.readFileSync('src/tender_search/core/static/core/workflow-actions.js','utf8');
const shell=content=>`<!doctype html><html><head><meta charset="utf-8"><style>#workflow-action-status{position:fixed;bottom:0;right:0}.spacer{height:800px}#collection-workspace{min-height:2000px}</style></head><body data-account-scope="scope-A" data-user-id="1"><span id="sidebar-favorite-count">(0)</span><main><div class="spacer"></div>${content}</main><script src="/workflow.js"></script></body></html>`;
const form=(url,body='')=>`<form method="post" data-workflow-action action="${url}"><input type="hidden" name="csrfmiddlewaretoken" value="test">${body}<button type="submit" name="action" value="restore">Переместить</button></form>`;
const workspace=moved=>shell(`<section id="collection-workspace"><nav><strong id="preparing-count">${moved?1:0}</strong></nav><table><tbody>${moved?'':`<tr id="moving"><td>${form('/workspace/items/1/stage/','<input name="stage" value="preparing">')}</td></tr>`}<tr><td><div data-lot-id="2"></div><textarea id="other-note" name="note"></textarea></td></tr></tbody></table></section>`);
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  for(const failure of [false,true]){
   const page=await browser.newPage({viewport:{width:1200,height:800}});let moved=false,posts=0,navigations=0,failRefresh=failure;
   page.on('pageerror',error=>console.error('browser error',error.message));page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++;});
   await page.route('http://workflow.test/**',route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/workflow.js')return route.fulfill({body:script,contentType:'application/javascript'});
    if(req.method()==='POST'){posts++;moved=true;assert.equal(req.headers()['x-account-scope'],'scope-A');assert.match(req.postData(),/restore/);return route.fulfill({json:{ok:true,scope:'scope-A',messages:[]}});}
    if(moved&&failRefresh){failRefresh=false;return route.fulfill({status:503,body:'unavailable'});}
    return route.fulfill({contentType:'text/html',body:workspace(moved)});
   });
   const url='http://workflow.test/workspace/?profile=7&stage=preparing&sort=-amount&per_page=100&page=2';
   await page.goto(url);await page.fill('#other-note','Несохранённая заметка');await page.evaluate(()=>scrollTo(0,650));
   // Submit twice while the first request is pending: only one POST is allowed.
   await page.evaluate(()=>{const f=document.querySelector('#moving form'),b=f.querySelector('button');for(let i=0;i<2;i++)f.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:b}));});
   if(failure){await page.getByRole('button',{name:'Проверить состояние'}).waitFor({timeout:5000}).catch(async error=>{console.error(await page.locator('#workflow-action-status').evaluate(node=>node.outerHTML));throw error;});assert.equal(await page.locator('#moving button').isDisabled(),true);await page.getByRole('button',{name:'Проверить состояние'}).click();}
   await page.waitForFunction(()=>!document.getElementById('moving'));
   assert.equal(posts,1);assert.equal(navigations,1);assert.equal(page.url(),url);
   assert.equal(await page.inputValue('#other-note'),'Несохранённая заметка');assert.equal(await page.textContent('#preparing-count'),'1');
   await page.waitForTimeout(40);assert.equal(await page.evaluate(()=>scrollY),650);await page.close();
  }
  const page=await browser.newPage();let moved=false,posts=0;
  const card=()=>shell(`<section id="lot-detail"><div id="lot-header">Лот</div><div id="lot-workflow-region">${moved?'В работе':'Неразобранные'}</div><details id="spec" open><summary>Спецификация</summary>Текст</details><div id="lot-triage-region">${moved?'':form('/workspace/items/1/stage/')}</div><textarea id="note">Сохранённая</textarea></section>`);
  await page.route('http://workflow.test/**',route=>{if(new URL(route.request().url()).pathname==='/workflow.js')return route.fulfill({body:script,contentType:'application/javascript'});if(route.request().method()==='POST'){posts++;moved=true;return route.fulfill({json:{ok:true,scope:'scope-A',messages:[]}});}return route.fulfill({body:card(),contentType:'text/html'});});
  await page.goto('http://workflow.test/lots/1/?next=%2Fworkspace%2F');await page.fill('#note','Личный черновик');
  const spec=await page.$('#spec');await page.getByRole('button',{name:'Переместить'}).click();await page.waitForFunction(()=>document.getElementById('lot-workflow-region').textContent==='В работе');
  assert.equal(posts,1);assert.equal(await page.inputValue('#note'),'Личный черновик');assert.equal(await spec.evaluate(node=>node.isConnected&&node.open),true);await page.close();
  const swapped=await browser.newPage();let changed=false;
  await swapped.route('http://workflow.test/**',route=>{
   if(new URL(route.request().url()).pathname==='/workflow.js')return route.fulfill({body:script,contentType:'application/javascript'});
   if(route.request().method()==='POST'){changed=true;return route.fulfill({json:{ok:true,scope:'scope-A',messages:[]}});}
   return route.fulfill({body:changed?workspace(true).replace('scope-A','scope-B').replace('preparing-count','OTHER-PRIVATE-ACCOUNT'):workspace(false),contentType:'text/html'});
  });
  await swapped.goto('http://workflow.test/workspace/');await swapped.getByRole('button',{name:'Переместить'}).click();await swapped.getByRole('button',{name:'Проверить состояние'}).waitFor();
  assert.equal(await swapped.locator('#OTHER-PRIVATE-ACCOUNT').count(),0);await swapped.close();
  const fast=await browser.newPage();const movedIds=new Set();let gets=0,fastPosts=0;
  function fastPage(snapshot){return shell(`<section id="collection-workspace"><nav class="collection-funnel"><a class="funnel-step" href="?stage=all"><strong>${100-snapshot.size}</strong></a><a class="funnel-step" href="?stage=preparing"><strong id="fast-count">${snapshot.size}</strong></a></nav><table><tbody>${Array.from({length:100},(_,i)=>i+1).filter(id=>!snapshot.has(id)).map(id=>`<tr id="fast-row-${id}"><td><div data-lot-id="${id}"></div>${id<3?form('/workspace/items/'+id+'/stage/'):'Другой лот'}</td></tr>`).join('')}</tbody></table></section>`);}
  await fast.route('http://workflow.test/**',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.pathname==='/workflow.js')return route.fulfill({body:script,contentType:'application/javascript'});
   if(req.method()==='POST'){fastPosts++;const id=Number(url.pathname.split('/')[3]);movedIds.add(id);return route.fulfill({json:{ok:true,scope:'scope-A',messages:[],workspace:{profile:'7',counts:{all:100-movedIds.size,preparing:movedIds.size},collection_count:100,favorite_count:movedIds.size,lots:[{lot_id:id,stage:'preparing'}]}}});}
   const snapshot=new Set(movedIds);if(movedIds.size){gets++;await new Promise(resolve=>setTimeout(resolve,500));}
   return route.fulfill({body:fastPage(snapshot),contentType:'text/html'});
  });
  await fast.goto('http://workflow.test/workspace/?profile=7&stage=all&per_page=100');
  await fast.locator('#fast-row-1 button').click();await fast.waitForFunction(()=>!document.getElementById('fast-row-1'),null,{timeout:4000}).catch(async error=>{console.error({fastPosts,gets,ids:[...movedIds],status:await fast.locator('#workflow-action-status').textContent()});throw error;});
  assert.equal(gets,0,'Immediate move must not wait for a table GET');assert.equal(await fast.textContent('#fast-count'),'1');
  while(gets<1)await fast.waitForTimeout(50);
  await fast.locator('#fast-row-2 button').click();await fast.waitForFunction(()=>!document.getElementById('fast-row-2'));
  await fast.waitForTimeout(1500);
  assert.equal(await fast.locator('#fast-row-1,#fast-row-2').count(),0,'Older GET cannot restore moved rows');assert.equal(await fast.textContent('#fast-count'),'2');assert.equal(fastPosts,2);await fast.close();
  const work=await browser.newPage();let workGets=0,workPosts=0;
  await work.route('http://workflow.test/**',route=>{
   const req=route.request();if(new URL(req.url()).pathname==='/workflow.js')return route.fulfill({body:script,contentType:'application/javascript'});
   if(req.method()==='POST'){workPosts++;return route.fulfill({json:{ok:true,scope:'scope-A',messages:[],workspace:{profile:'all',counts:{preparing:0,not_participated:1},collection_count:1,favorite_count:0,lots:[{lot_id:1,stage:'not_participated'}]}}});}
   workGets++;return route.fulfill({contentType:'text/html',body:shell(`<section id="collection-workspace"><nav class="collection-funnel"><a class="funnel-step" href="/workspace/?stage=preparing"><strong>1</strong></a></nav><table><tr id="work-row"><td><div data-lot-id="1"></div>${form('/workspace/items/1/stage/')}</td></tr></table><button id="queue-run">Очередь</button></section>`)});
  });
  await work.goto('http://workflow.test/workspace/drafts/?profile=all');
  const queueButton=await work.$('#queue-run');await work.locator('#work-row button').click();
  await work.waitForFunction(()=>!document.getElementById('work-row'));await work.waitForTimeout(1000);
  assert.equal(workPosts,1);assert.equal(workGets,1,'Moving a prepared lot must not replace queue controls');
  assert.equal(await queueButton.evaluate(node=>node.isConnected),true);assert.equal(await work.textContent('.funnel-step strong'),'0');await work.close();
  console.log('Workflow browser: no navigation, correct counters, preserved notes/specification/scroll, duplicate POST guard, GET-only recovery passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

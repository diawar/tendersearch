const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Dmitriy Small/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const html=fs.readFileSync('tests/fixtures/preparation.html','utf8'),scope=html.match(/data-account-scope="([^"]+)"/)[1];
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://preview.test/**',route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/account/session/')return route.fulfill({json:{authenticated:true,scope}});
   if(url.pathname.endsWith('/specification/'))return route.fulfill({contentType:'text/html',body:'<section class="panel preparation-specification" id="preparation-specification"><h2>1. Требования заказчика</h2><details><summary>Текст загружен</summary>Тестовая спецификация</details></section>'});
   if(url.pathname.startsWith('/static/core/')){const file=path.join('src/tender_search/core/static/core',path.basename(url.pathname));return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'});}
   if(url.pathname==='/preview/')return route.fulfill({body:html,contentType:'text/html'});
   return route.fulfill({json:{state:'none',entries:[],has_documents:false,cities:[],cargo_types:[],locations:[]}});
  });
  await page.goto('http://preview.test/preview/');await page.waitForFunction(()=>!document.documentElement.classList.contains('account-locked'));
  assert.equal(await page.locator('[data-step]').count(),6);
  assert.equal(await page.locator('#prepare-with-extension').isDisabled(),true);
  await page.locator('#preparation-form [name=product_name]').fill('Тестовый монитор');await page.locator('[name=manufacturer]').fill('Тестовый изготовитель');
  await page.locator('[name=country]').selectOption({label:'КАЗАХСТАН'});
  await page.locator('#preparation-form [name=unit_gross]').fill('116');
  await page.locator('#prep-step-3 details').evaluate(el=>el.open=true);
  await page.locator('[name=legal_address]').fill('Павлодар, Желтоксан, 7');await page.locator('[name=bank_account]').fill('KZ000000000000000000');await page.locator('[name=contact_phone]').fill('+77000000000');
  const tax=page.locator('[name=taxpayer_type]');await tax.selectOption({index:1});
  if(await page.locator('[name=confirm_discount]').isVisible())await page.locator('[name=confirm_discount]').check();
  await page.evaluate(()=>{document.querySelector('#preparation-specification').innerHTML='<details><summary>Текст загружен</summary>Тестовая спецификация</details>';document.dispatchEvent(new CustomEvent('htmx:afterSwap',{detail:{target:document.querySelector('#preparation-specification'),elt:document.querySelector('#preparation-specification')}}));});
  await page.waitForTimeout(50);
  assert.match(await page.locator('[data-current-step]').textContent(),/сохраните/);
  assert.equal(await page.locator('#save-preparation').isDisabled(),false);
  assert.equal(await page.locator('#prepare-with-extension').isDisabled(),true,'unsaved values cannot launch');
  assert.equal(await page.locator('#preparation-form form').count(),0,'no nested forms');
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'data/preparation-steps-desktop.png',fullPage:false});
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'data/preparation-steps-mobile.png',fullPage:false});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'mobile horizontal overflow');
  assert.deepEqual(errors,[]);console.log('PASS: six steps, required fields, save CTA, unsaved launch blocked, valid forms, mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

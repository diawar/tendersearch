const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Dmitriy Small/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const product={name:'Сумка для ноутбука 15.6 X-Game ELEMENT Axis Black Grey',url:'https://over-shop.kz/product',category_label:'Ноутбуки и компьютеры / Аксессуары / Сумки для ноутбуков',prices:{online:6436,club:6115},price:{unit_gross:6436,kind:'online'},stock_text:'50 шт. в 1 магазине',eligible:false,blocked_reasons:['ИИ не подтвердил полноту разбора требований.','Не все требования к товару подтверждены.'],unverified:['Материал — кожа или полиэстер','Цвет — чёрный'],attributes:{'Диагональ':'15.6','Материал':'Полиэстер'},checks:[{scope:'product',state:'unknown',requirement:'Предназначена для ноутбуков с диагональю экрана до 15,6 дюйма',actual:'Совместимость не подтверждена карточкой',reason:'Название содержит 15.6, но совместимость до этой диагонали не подтверждена.'},{scope:'product',state:'fail',requirement:'Цвет — чёрный',actual:'Black Grey',reason:'В карточке указан чёрно-серый цвет.'},{scope:'product',state:'pass',requirement:'Сумка для переноски ноутбука',actual:'Сумка для ноутбука'}]};
 const data={state:'done',result:{engine:'openai',model:'Тестовый результат',limitation:'Проверьте соответствие перед подачей.',summary:'Кандидат требует ручной проверки.',candidates:[product]}};
 await page.route('http://preview.test/**',route=>route.fulfill({json:data}));
 await page.setContent('<html lang="ru"><meta charset="utf-8"><form id="preparation-form"><input name="product_name" hidden><input name="unit_gross" hidden></form><main style="max-width:1200px"><section id="product-selection" data-ai-configured="true" data-analysis-url="http://preview.test/analysis"><button id="analyze-product">Проанализировать спецификацию и подобрать товар</button><p id="product-selection-status"></p><div id="product-selection-results"></div></section></main></html>');
 await page.addStyleTag({content:fs.readFileSync('src/tender_search/core/static/core/app.css','utf8')});
 await page.addScriptTag({content:fs.readFileSync('src/tender_search/core/static/core/product-selection.js','utf8')});
 await page.waitForSelector('.product-candidate');
 assert.equal(await page.locator('.candidate-checks tbody tr:visible').count(),2);
 assert.equal(await page.locator('.candidate-checks tbody tr:visible').first().getAttribute('data-state'),'fail');
 await page.getByRole('button',{name:'Все (3)',exact:true}).click();assert.equal(await page.locator('.candidate-checks tbody tr:visible').count(),3);
 assert.equal(await page.getByRole('button',{name:'Подставить товар и цену'}).isDisabled(),true);
 await page.screenshot({path:'data/product-comparison-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Требуют внимания (2)',exact:true}).click();assert.equal(await page.locator('.candidate-checks tbody tr:visible').count(),2);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:'data/product-comparison-mobile.png',fullPage:true});
 async function selection(fields){
  const selected=await browser.newPage();
  await selected.route('http://preview.test/**',route=>route.fulfill({json:{state:'done',result:{...data.result,candidates:[{...product,eligible:true,blocked_reasons:[],preparation_fields:fields}]}}}));
  await selected.setContent('<meta charset="utf-8"><form id="preparation-form"><input name="product_name"><input name="unit_gross"><input name="manufacturer" value="Старый завод"><select name="country"><option value=""></option><option value="КИТАЙ">КИТАЙ</option><option value="КАЗАХСТАН" selected>КАЗАХСТАН</option></select></form><section id="product-selection" data-ai-configured="true" data-analysis-url="http://preview.test/analysis"><button id="analyze-product"></button><p id="product-selection-status"></p><div id="product-selection-results"></div></section>');
  await selected.addScriptTag({content:fs.readFileSync('src/tender_search/core/static/core/product-selection.js','utf8')});
  await selected.getByRole('button',{name:'Подставить товар и цену'}).click();
  assert.equal(await selected.locator('[name=product_name]').inputValue(),fields.product_name);
  assert.equal(await selected.locator('[name=manufacturer]').inputValue(),fields.manufacturer);
  assert.equal(await selected.locator('[name=country]').inputValue(),fields.country);
  assert.equal(await selected.locator('[name=unit_gross]').inputValue(),'6436');
  if(!fields.country)assert.match(await selected.locator('#product-selection-status').textContent(),/Заполните вручную/);
  await selected.close();
 }
 await selection({product_name:'Сумка X',manufacturer:'Завод X',country:'КИТАЙ'});
 await selection({product_name:'Сумка Y',manufacturer:'',country:''});
 // Manual confirmation is available only for unknown evidence, never for hard failures.
 assert.equal(await page.getByRole('button',{name:'Проверил вручную — подставить',exact:true}).count(),0);
 async function manual(candidate,allowed){
  const review=await browser.newPage();
  await review.route('http://preview.test/**',route=>route.fulfill({json:{state:'done',result:{...data.result,candidates:[candidate]}}}));
  await review.setContent('<meta charset="utf-8"><form id="preparation-form"><input name="product_name"><input name="unit_gross"></form><section id="product-selection" data-ai-configured="true" data-analysis-url="http://preview.test/analysis"><button id="analyze-product"></button><p id="product-selection-status"></p><div id="product-selection-results"></div></section>');
  await review.addScriptTag({content:fs.readFileSync('src/tender_search/core/static/core/product-selection.js','utf8')});
  await review.waitForSelector('.product-candidate');
  const confirm=review.locator('.candidate-manual-review button');
  assert.equal(await confirm.count(),allowed?1:0);
  if(allowed){
   await review.locator('.candidate-manual-review summary').click();
   assert.equal(await confirm.isDisabled(),true);
   const boxes=review.locator('.candidate-manual-review input');
   await boxes.first().check();assert.equal(await confirm.isDisabled(),true);
   for(const box of await boxes.all())await box.check();
   await confirm.click();assert.equal(await review.locator('[name=product_name]').inputValue(),product.name);
   assert.equal(await review.locator('[name=unit_gross]').inputValue(),'6436');
   assert.match(await review.locator('#product-selection-status').textContent(),/проверены вами вручную/);
  }
  await review.close();
 }
 const unknown={...product,checks:product.checks.filter(c=>c.state!=='fail')};
 await manual(unknown,true);
 await manual({...unknown,blocked_reasons:[...unknown.blocked_reasons,'Недостаточно товара для всего лота.']},false);
 await manual({...unknown,price:null},false);
 await manual({...unknown,checks:[...unknown.checks,{scope:'quantity',state:'unknown'}]},false);
 assert.deepEqual(errors,[]);console.log('PASS: evidence table, problem-first filter, all checks, disabled unsafe apply, mobile overflow, no JS errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

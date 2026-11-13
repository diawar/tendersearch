const assert=require('node:assert/strict'),{checkSavedApplication}=require('../extensions/goszakup-assistant/check.js');
const{chromium}=require('C:/Users/Dmitriy Small/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});try{
 const page=await browser.newPage();await page.setContent('<main></main>');
 const source=checkSavedApplication.toString().replaceAll('location.','fixtureLocation.');
 const values=await page.evaluate(async source=>{
  const fixtureLocation={origin:'https://zakup.gov.kz',pathname:'/cabinet/purchase/my-applications'},run=eval('('+source+')');
  const target={company_bin:'241040001415',announcement_number:'17932745-1',lot_number:'87944589-ЗЦП3',number:'123'},out={};
  for(const mode of ['found','different-number','submitted','empty','error','duplicates','wrong-bin']){
   document.querySelector('main').innerHTML='<header>БИН: '+(mode==='wrong-bin'?'000000000000':'241040001415')+'</header><input placeholder="№ объявления"><button>Найти</button><table><thead><tr><th>№ заявки</th><th>№ объявления</th><th>Статус заявки</th></tr></thead><tbody></tbody></table>';
   document.querySelector('button').onclick=()=>setTimeout(()=>{
    const row='<tr><td>'+(mode==='different-number'?'456':'123')+'</td><td>17932745-1</td><td>'+(mode==='submitted'?'Подана':'Проект')+'</td></tr>';
    document.querySelector('tbody').innerHTML=['empty','error'].includes(mode)?'<tr><td colspan="3"><div class="ant-empty-description">Нет заявок</div></td></tr>':row+(mode==='duplicates'?row:'');
    if(mode==='error')document.body.insertAdjacentHTML('beforeend','<div class="ant-alert-error">Источник недоступен</div>');
   },30);
   out[mode]=await run(target);document.querySelector('.ant-alert-error')?.remove();
  }return out;
 },source);
 assert.equal(values.found.check_state,'found');assert.equal(values['different-number'].found_number,'456');assert.equal(values.submitted.application_state,'submitted');assert.equal(values.empty.check_state,'absent');
 for(const key of ['error','duplicates','wrong-bin'])assert.equal(values[key].check_state,'unknown');
 console.log('PASS: actual browser DOM detection of existing/new-number draft, submitted, empty, portal error, duplicate rows, wrong BIN');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

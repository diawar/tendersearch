// Generates a standalone fixture for headless Chrome; never accesses a portal/profile.
const fs=require('node:fs');
const {runDraftStep}=require('../extensions/goszakup-assistant/draft.js');
const packet={schema:'goszakup-preparation-v1',company_bin:'241040001415',announcement_external_id:'17920228',
 announcement_number:'17920228-1',lot_number:'88537093-ЗЦП1',purchase_method_id:12,single_position:true,
 data:{product_name:'ТЕСТ комплект',manufacturer:'ТЕСТ изготовитель',country:'Китай',legal_address:'Тестовая\n1',bank_account:'KZ000000000000000000',contact_phone:'+77000000000'}};
const script=`
let fixtureLocation={href:'https://zakup.gov.kz/cabinet/announcement/17920228',pathname:'/cabinet/announcement/17920228'};
const packet=${JSON.stringify(packet)};
const run=${runDraftStep.toString().replaceAll('location.', 'fixtureLocation.')};
const root=document.querySelector('#fixture'),out=document.querySelector('#output');
const header='БИН: '+packet.company_bin;
function page(html,path){root.innerHTML='<header>'+header+'</header>'+html;fixtureLocation.pathname=path;fixtureLocation.href='https://zakup.gov.kz'+path;}
function check(value,message){if(!value)throw Error(message);}
const apply='/cabinet/purchase/announcements/17920228/apply';
async function tests(){
 let clicks=0;
 page('<p>17920228-1</p><button>Подать заявку</button>','/cabinet/announcement/17920228');
 let r=await run(packet);check(r.next==='open_form','announcement probe');
 root.querySelector('header').append(document.createTextNode(' БИН: '+packet.company_bin));
 const customer=document.createElement('main');customer.textContent='Заказчик БИН: 111111111111';root.append(customer);
 r=await run(packet);check(r.next==='open_form','duplicate own BIN and customer BIN outside header do not block');
 root.querySelector('header').textContent='БИН: 111111111111';customer.textContent='БИН: '+packet.company_bin;
 r=await run(packet);check(r.blocked,'own BIN in content cannot override wrong organization header');
 root.querySelector('header').textContent=header;customer.remove();
 document.querySelector('button').onclick=()=>{clicks++;fixtureLocation.pathname=apply;fixtureLocation.href='https://zakup.gov.kz'+apply+'?step=1';};
 r=await run(packet,'open_form');check(!r.blocked&&clicks===1,'open form');
 for(const markup of [
  '<a href="'+apply+'?step=1">Подать заявку</a>',
  '<a href="'+apply+'?step=1"><button>Подать заявку</button></a>',
  '<div role="button">Подать заявку</div>'
 ]){
  page('<p>17920228-1</p>'+markup,'/cabinet/announcement/17920228');
  r=await run(packet);check(r.next==='open_form','link/role/nested button recognized');
 }
 page('<p>17920228-1</p><a target="_blank" href="https://zakup.gov.kz'+apply+'?step=3&id=73769061">Продолжить заявку № 73769061</a>','/cabinet/announcement/17920228');
 r=await run(packet,'open_form',{applicationNumber:'73769061'});check(r.navigate?.endsWith('?step=3&id=73769061')&&r.number==='73769061','blank target resumes in tracked tab');
 for(const suffix of ['?step=3&id=999','?step=4&id=73769061','?step=3&id=73769061&other=x','?step=3&id=73769061&id=73769061']){
  page('<p>17920228-1</p><a target="_blank" href="https://zakup.gov.kz'+apply+suffix+'">Продолжить заявку № 73769061</a>','/cabinet/announcement/17920228');
  r=await run(packet,'open_form',{applicationNumber:'73769061'});check(r.blocked,'invalid resume destination blocked '+suffix);
 }
 page('<p>17920228-1</p><a href="https://other.example/">Подать заявку</a>','/cabinet/announcement/17920228');
 r=await run(packet,'open_form');check(r.blocked,'foreign link blocked');
 page('<p>17920228-1</p><button>Подать заявку</button><button>Подать заявку</button>','/cabinet/announcement/17920228');
 r=await run(packet);check(r.blocked&&/несколько/.test(r.message),'true ambiguous actions blocked');
 page('<p>17920228-1</p>','/cabinet/announcement/17920228');
 setTimeout(()=>{const a=document.createElement('a');a.href=apply+'?step=1';a.textContent='Подать заявку';root.append(a);},300);
 r=await run(packet);check(r.next==='open_form','late mounted action recognized');
 page('<button>Подписать и отправить</button>',apply+'?step=5&id=123');
 r=await run(packet,'save_account');check(r.blocked&&clicks===1,'signing blocked');
 page('<button>Сохранить</button>',apply+'/guarantee/42?step=3&id=123');
 r=await run(packet,'save_account');check(r.blocked,'guarantee blocked');
 page('<p>17920228-1</p><button>Подать заявку</button>','/cabinet/announcement/17920228');
 root.querySelector('header').textContent='БИН: 111111111111';r=await run(packet,'open_form');check(r.blocked,'wrong BIN blocked');
 const input=(label,value='')=>'<div class="ant-form-item"><label>'+label+'</label><input value="'+value+'"></div>';
 page('<p>88537093-ЗЦП1</p>'+input('Наименование товара')+input('Завод-изготовитель')+'<div class="ant-form-item"><label>Страна происхождения</label><div class="ant-select-selector"><span class="ant-select-selection-item">Китай</span><input role="combobox"></div></div><button>Сохранить</button>',apply+'/product-description/42');
 fixtureLocation.href+='?id=123&step=3';
 const registryTwo={...packet,position_count:2,repeat_product:true};
 r=await run(registryTwo);check(r.next==='fill_description'&&/одна позиция/.test(r.message),'two registry entries can map to one complete portal group with consent');
 r=await run({...registryTwo,repeat_product:false});check(r.blocked,'same-product consent remains mandatory');
 const summary=document.createElement('dl');summary.innerHTML='<dt>Номер лота</dt><dd>11111111-ЗЦП1</dd>';root.append(summary);
 r=await run(registryTwo);check(r.blocked,'wrong form summary blocks even if target number exists elsewhere');summary.remove();
 const countryLabel=[...root.querySelectorAll('label')].find(el=>el.textContent==='Страна происхождения');countryLabel.textContent='Другой параметр';
 r=await run(registryTwo,'fill_description');check(r.blocked&&document.querySelector('input').value==='','partial groups never filled');countryLabel.textContent='Страна происхождения';
 r=await run(packet,'fill_description',{applicationNumber:'123'});check(!r.blocked,'fill description: '+r.message);
 check(document.querySelector('input').value===packet.data.product_name,'product filled');
 r=await run(packet);check(r.next==='save_description','description ready');
 document.querySelector('button').onclick=()=>{const toast=document.createElement('div');toast.className='ant-notification-notice-success';toast.textContent='Описание сохранено';document.body.append(toast);};
 r=await run(registryTwo,'save_description');check(r.state==='complete'&&r.number==='123'&&/одна позиция/.test(r.message),'single portal group save confirmed for two registry entries');
 document.querySelector('input').value='Другая модель';r=await run(packet,'fill_description');check(r.blocked,'existing content protected');
 r=await run(packet,'probe',{applicationNumber:'999'});check(r.blocked,'other application blocked');
 page('<h3>Документация</h3>',apply);fixtureLocation.href+='?id=123&step=3';
 setTimeout(()=>{const anchor=document.createElement('a');anchor.href='#';anchor.innerHTML='<button>Описание товара — 88537093-ЗЦП1 *</button>';root.append(anchor);},300);
 r=await run(packet);check(r.next==='open_description','async description button and nested actions recognized');
 const descriptionButton=root.querySelector('button');
 descriptionButton.onclick=event=>{
  event.preventDefault();fixtureLocation.pathname=apply+'/product-description/42';fixtureLocation.href='https://zakup.gov.kz'+fixtureLocation.pathname+'?id=123&step=3&groupId=6';
  root.innerHTML='<header>'+header+'</header><div>Загрузка…</div>';
  setTimeout(()=>{root.insertAdjacentHTML('beforeend','<p>88537093-ЗЦП1</p>'+input('Наименование товара')+input('Завод-изготовитель')+'<div class="ant-form-item"><label>Страна происхождения</label><div class="ant-select-content ant-select-content-has-value">Китай<input role="combobox"></div></div><button>Сохранить</button>');},300);
 };
 r=await run(packet,'open_description');check(r.state==='description_opened','description route opened');
 r=await run(packet);check(r.next==='fill_description','async product data and form awaited after route change');
 page('<div role="tab" aria-selected="true">Просмотр выбранных 1</div><div role="tab">Лоты</div><table><tbody><tr><td>11111111-ЗЦП1</td></tr></tbody></table>',apply);
 fixtureLocation.href+='?id=123&step=2';document.querySelector('[role=tab]').onclick=()=>{};
 r=await run(packet,'add_lot');check(r.blocked&&/другие лоты/.test(r.message),'other lots preserved');
 page('<div role="tab" aria-selected="true">Просмотр выбранных 1</div><table><tbody><tr><td>88537093-ЗЦП1</td></tr></tbody></table><button>Далее</button>',apply);
 fixtureLocation.href+='?id=123&step=2';r=await run(packet);check(r.next==='advance_lots'&&r.lotConfirmed,'exact selected lot confirmed');
 const combo=(label,value)=>'<div class="ant-form-item"><label>'+label+'</label><div class="ant-select-selector"><span class="ant-select-selection-item">'+value+'</span><input role="combobox"></div></div>';
 page(combo('Юридический адрес',packet.data.legal_address)+combo('Банковский счёт',packet.data.bank_account)+input('Контактный телефон',packet.data.contact_phone)+'<button>Далее</button>',apply);
 fixtureLocation.href+='?step=1';
 document.querySelector('button').onclick=()=>{fixtureLocation.href='https://zakup.gov.kz'+apply+'?id=456&step=2';};
 r=await run(packet,'save_account',{applicationNumber:'123'});check(r.blocked,'known draft cannot be recreated');
 r=await run(packet,'save_account');check(r.state==='draft_created'&&r.number==='456','creation confirmed by new id and step');
 page('<p>88537093-ЗЦП1</p>'+input('Наименование товара')+input('Завод-изготовитель')+combo('Страна происхождения','Китай')+input('Наименование товара')+input('Завод-изготовитель')+combo('Страна происхождения','Китай')+'<button>Сохранить</button>',apply+'/product-description/42');
 fixtureLocation.href+='?id=123&step=3';
 r=await run(packet,'fill_description');check(r.blocked,'unexpected multiple positions blocked');
 r=await run({...packet,position_count:2,repeat_product:true},'fill_description');check(!r.blocked,'explicit multi-position fill');
 const productFields=[...document.querySelectorAll('.ant-form-item')].filter(el=>el.querySelector('label').textContent==='Наименование товара');
 check(productFields.every(el=>el.querySelector('input').value===packet.data.product_name),'all consented product positions filled');
 // Current portal Select DOM: no .ant-select-selector or .ant-select-selection-item.
 const modern=label=>'<div class="ant-form-item"><label>'+label+'</label><div class="ant-select"><div class="ant-select-content"><input role="combobox" style="width:0;padding:0;border:0"></div></div></div>';
 page(modern('Юридический адрес')+modern('Банковский счёт')+input('Контактный телефон')+'<button>Далее</button>',apply);
 fixtureLocation.href+='?step=1';
 for(const [i,control] of [...root.querySelectorAll('.ant-select-content')].entries()){
  const value=i===0?packet.data.legal_address.replace(/\\s+/g,' '):packet.data.bank_account+' — ТЕСТ банк';
  control.querySelector('input').value='Устаревший поиск';
  control.onmousedown=()=>{
   document.querySelectorAll('.ant-select-dropdown').forEach(el=>el.remove());
   const popup=document.createElement('div');popup.className='ant-select-dropdown';
   popup.innerHTML='<div role="listbox" id="list-'+i+'" style="width:0;height:0"></div><div class="ant-select-item-option" title="'+value+'">'+value+'</div>';
   document.body.append(popup);control.querySelector('input').setAttribute('aria-controls','list-'+i);
   popup.querySelector('.ant-select-item-option').onclick=()=>{
    if(i===0){control.classList.add('ant-select-content-has-value');control.prepend(document.createTextNode(value));}
    else{const selected=document.createElement('div');selected.className='ant-select-content-value';selected.textContent=value;control.append(selected);}
    popup.remove();
   };
  };
 }
 r=await run(packet,'fill_account');check(!r.blocked,'modern Select: '+r.message);
 check([...root.querySelectorAll('.ant-select-content input')].every(el=>el.value===''),'address and account compared without full-line search; stale queries cleared');
 r=await run(packet);check(r.next==='save_account','modern selected values recognized');
 const tax=document.createElement('div');tax.className='ant-form-item';tax.innerHTML='<label class="ant-form-item-required">Тип налогоплательщика</label><div class="ant-select"><input role="combobox"></div>';root.append(tax);
 r=await run(packet);check(r.blocked&&!r.uncertain&&/Тип налогоплательщика/.test(r.message),'required extra choice blocks before create intent');tax.remove();
 const taxpayer=document.createElement('div');taxpayer.className='ant-form-item';taxpayer.innerHTML='<label class="ant-form-item-required">Тип налогоплательщика</label><div class="ant-select"><div class="ant-select-content"><input readonly role="combobox"></div></div>';root.append(taxpayer);
 taxpayer.querySelector('.ant-select-content').onmousedown=()=>{
  const popup=document.createElement('div');popup.className='ant-select-dropdown';popup.innerHTML='<div class="ant-select-item-option">Юридическое лицо</div>';document.body.append(popup);
  popup.firstChild.onclick=()=>{const control=taxpayer.querySelector('.ant-select-content');control.classList.add('ant-select-content-has-value');control.prepend(document.createTextNode('Юридическое лицо'));popup.remove();};
 };
 r=await run({...packet,data:{...packet.data,taxpayer_type:'Юридическое лицо'}},'fill_account');check(!r.blocked,'taxpayer choice filled');
 r=await run({...packet,data:{...packet.data,taxpayer_type:'Юридическое лицо'}});check(r.next==='save_account','taxpayer ready before create');taxpayer.remove();
 const currentAddress=root.querySelector('.ant-select-content-has-value');currentAddress.firstChild.textContent='Другой адрес';
 r=await run(packet,'fill_account');check(r.blocked,'different selected value not overwritten');
 currentAddress.classList.remove('ant-select-content-has-value');currentAddress.firstChild.remove();currentAddress.querySelector('input').value=packet.data.legal_address;
 r=await run(packet);check(r.next==='fill_account','matching search text alone is not a confirmed selected value');
 out.textContent='PASS: portal fixture guards, account creation, multiple positions and current portal Select';
}
tests().catch(error=>{out.textContent='FAIL: '+error.stack;});`;
fs.writeFileSync('data/draft-dom-fixture.html','<!doctype html><meta charset="utf-8"><div id="fixture"></div><pre id="output">RUNNING</pre><script>'+script+'</script>');


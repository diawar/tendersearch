let packet;
let statusReport;
let displayedDraftKey;
const output=document.querySelector('#result');
const show=(value,tone='info')=>{
 output.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);
 const card=document.querySelector('#result-card');
 card.hidden=false;
 let title='Результат',summary=typeof value==='string'?value:'Подробности доступны ниже.';
 if(value&&typeof value==='object'){
  const titles={draft:'Черновик найден',submitted:'Заявка подана',completed:'Заявка завершена',unknown:'Статус не подтверждён'};
  if(value.state){
   title=titles[value.state]||'Статус не подтверждён';tone=['draft','submitted','completed'].includes(value.state)?'success':'warning';
   summary=value.state==='unknown'?'Проверьте фильтры и наличие нужного лота в списке. Это не означает отсутствие заявки.':
    'Заявка № '+value.number+(value.state==='completed'?'. Результат участия проверяется отдельно.':'. По данным открытой страницы.');
  }else if(value.error){title='Не удалось проверить';summary=value.error;tone='error';}
  else if(value.path){title='Диагностика готова';summary='Структура страницы прочитана. Это не проверка статуса заявки.';}
  else if(value.message){summary=value.message;}
 }
 if(tone==='error')title='Не удалось выполнить';
 card.dataset.tone=tone;
 document.querySelector('#result-title').textContent=title;
 document.querySelector('#result-summary').textContent=summary;
 document.querySelector('#result-mark').textContent=({success:'✓',warning:'!',error:'!'}[tone]||'i');
};
document.querySelector('#file').addEventListener('change',async event=>{
 document.querySelector('#draft-start').disabled=true;
 packet=null;statusReport=null;document.querySelector('#download').disabled=true;document.querySelector('#send').disabled=true;document.querySelector('#fill').disabled=true;document.querySelector('#packet').textContent='';
 try {
  const file=event.target.files[0];if(!file)return;if(file.size>50000)throw Error('Слишком большой файл');
  const data=JSON.parse(await file.text());
  if(data.schema!=='goszakup-preparation-v1'||!/^\d{12}$/.test(data.company_bin)||!/^\d+$/.test(data.announcement_external_id)||typeof data.data!=='object'||!data.data)throw Error('Неверный формат данных');
  for(const key of ['product_name','manufacturer','country','contact_phone'])if(typeof data.data[key]!=='string'||data.data[key].length>1000)throw Error('Неверные поля');
  data.repeat_product=document.querySelector('#repeat-product').checked;
  packet=data;document.querySelector('#packet').textContent='Объявление '+data.announcement_number+' · Лот '+data.lot_number+' · БИН '+data.company_bin+' · Записей плана в API: '+(data.position_count||'не подтверждено');
  for(const key of ['company_bin','announcement_number','lot_number'])document.querySelector('#'+key).value=data[key]||'';
  document.querySelector('#fill').disabled=false;
  const loaded=await chrome.runtime.sendMessage({type:'draft:load',packet:data});
  if(loaded.error)show('Текстовые поля доступны. Черновик: '+loaded.error,'warning');
  else{renderDraft(loaded.job);show('Подготовка загружена в память расширения до закрытия браузера.');}
 }catch(error){show(error.message,'error');}
});
async function execute(mode){
 try {
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const url=new URL(tab.url);if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))throw Error('Откройте кабинет https://zakup.gov.kz/cabinet/');
  const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:inspectOrFill,args:[mode,mode==='fill'?packet:null]});
  show(results[0].result);
 }catch(error){show(error.message,'error');}
}
document.querySelector('#inspect').onclick=()=>execute('inspect');
document.querySelector('#fill').onclick=()=>execute('fill');
function inspectOrFill(mode,packet){
 const url=new URL(location.href);
 if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))return {error:'Неверная страница'};
 const visible=el=>Boolean(el.getClientRects().length);
 const normalize=value=>String(value||'').toLowerCase().replace(/[*:]/g,'').replace(/\s+/g,' ').trim();
 const label=el=>{
  const explicit=el.id?[...document.querySelectorAll('label')].filter(l=>l.htmlFor===el.id):[];
  if(explicit.length===1)return explicit[0].textContent;
  return el.closest('.ant-form-item')?.querySelector('label')?.textContent||el.getAttribute('aria-label')||el.getAttribute('placeholder')||'';
 };
 const fields=[...document.querySelectorAll('input:not([type=password]):not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]),textarea,select,[role=combobox]')].filter(visible);
 if(mode==='inspect')return {path:url.pathname,fields:fields.map(el=>({tag:el.tagName,type:el.type||'',label:label(el).slice(0,150),role:el.getAttribute('role'),disabled:Boolean(el.disabled)})),tables:[...document.querySelectorAll('table')].filter(visible).map(t=>({headers:[...t.querySelectorAll('thead th')].map(h=>h.textContent.trim().replace(/\s+/g,' ')),rows:t.querySelectorAll('tbody tr').length})),buttons:[...document.querySelectorAll('button')].filter(visible).map(b=>b.textContent.trim().slice(0,80))};
 if(!packet)return {error:'Сначала загрузите файл'};
 const text=document.body.innerText;
 const bins=[...new Set([...document.querySelectorAll('header,[role=banner],.ant-layout-header')].filter(visible)
  .flatMap(el=>[...(el.innerText||el.textContent||'').matchAll(/БИН\s*:?\s*(\d{12})/g)].map(m=>m[1])))];
 if(bins.length!==1||bins[0]!==packet.company_bin)return {error:'Не подтверждён БИН организации в кабинете'};
 if(!text.includes(packet.announcement_number)&&!url.pathname.split('/').includes(packet.announcement_external_id))return {error:'Не подтверждено нужное объявление'};
 if(!text.includes(packet.lot_number))return {error:'Не подтверждён нужный лот'};
 // Stop before any price/signing screen. Only ordinary text fields are supported.
 if(text.includes('Цену вводите в окне TumarCSP')||text.includes('Подписать цены'))return {error:'На шаге цены заполнение отключено'};
 const mapping={'наименование товара':'product_name','введите наименование товара':'product_name','завод-изготовитель':'manufacturer','укажите название завода-изготовителя':'manufacturer','контактный телефон':'contact_phone'};
 const plan=[];
 for(const [name,key] of Object.entries(mapping)){
  const matches=fields.filter(el=>normalize(label(el))===name&&!el.disabled&&!el.readOnly&&['INPUT','TEXTAREA'].includes(el.tagName)&&!el.getAttribute('role'));
  if(matches.length>1)return {error:'Неоднозначное поле: '+name};
  if(matches.length===1&&!plan.some(x=>x.el===matches[0]))plan.push({el:matches[0],key});
 }
 if(plan.some(({el,key})=>el.value&&el.value!==packet.data[key]))return {error:'Есть заполненные поля с другими данными. Измените их вручную.'};
 for(const {el,key} of plan){
  const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto,'value').set.call(el,packet.data[key]);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
 }
 return {filled:plan.map(x=>x.key),message:'Проверьте значения. Выпадающие списки и сохранение заполните вручную. Никакие кнопки не нажимались.'};
}

document.querySelector('#status').onclick=async()=>{
 statusReport=null;document.querySelector('#download').disabled=true;document.querySelector('#send').disabled=true;
 document.querySelector('#status').disabled=true;
 document.querySelector('#status-label').textContent='Проверяем…';
 let validTarget=false,semanticFailure=false,target;
 try{
  target=statusTarget(Object.fromEntries(['company_bin','announcement_number','lot_number'].map(key=>[key,document.querySelector('#'+key).value])));
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const url=new URL(tab.url);if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))throw Error('Откройте кабинет zakup.gov.kz');
  validTarget=true;
  const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readPortalStatus,args:[target]});
  const report=results[0].result;if(report.error){semanticFailure=true;throw Error(report.error);}
  statusReport=report;document.querySelector('#download').disabled=false;document.querySelector('#send').disabled=false;show(report);
 }catch(error){
  if(validTarget&&!semanticFailure&&target){statusReport={schema:'goszakup-status-v1',...target,observed_at:new Date().toISOString(),state:'unknown'};document.querySelector('#download').disabled=false;document.querySelector('#send').disabled=false;}
  show(error.message,'error');
 }finally{
  document.querySelector('#status').disabled=false;
  document.querySelector('#status-label').textContent='Проверить статус';
 }
};
for(const key of ['company_bin','announcement_number','lot_number'])document.querySelector('#'+key).addEventListener('input',()=>{
 statusReport=null;document.querySelector('#download').disabled=true;document.querySelector('#send').disabled=true;show('Условия изменены. Нажмите «Проверить статус».');
});
document.querySelector('#download').onclick=()=>{
 if(!statusReport)return;
 const blob=new Blob([JSON.stringify(statusReport,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='goszakup-status.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};

document.querySelector('#send').onclick=async()=>{
 if(!statusReport)return;
 try{
  const url=reportTransferUrl(document.querySelector('#app-url').value.trim(),statusReport);
  await chrome.tabs.create({url});
  show('Открыта передача в приложение. Результат сохранения проверяйте в новой вкладке.');
 }catch(error){show(error.message,'error');}
};

function renderDraft(job){
 const active=job?.status==='running';
 document.querySelector('#draft-state').textContent=job?
  'Лот '+job.packet.lot_number+(job.number?' · Черновик № '+job.number:'')+' · '+job.message:'Подготовка не загружена.';
 document.querySelector('#draft-start').disabled=!job||active;
 document.querySelector('#draft-stop').disabled=!active;
 document.querySelector('#draft-clear').disabled=!job||active;
 document.querySelector('#file').disabled=active;
 document.querySelector('#repeat-product').disabled=active;
 document.querySelector('#status').disabled=active;
 if(job?.packet){
  packet=job.packet;document.querySelector('#fill').disabled=active;document.querySelector('#repeat-product').checked=packet.repeat_product===true;
  const key=[packet.company_bin,packet.announcement_number,packet.lot_number,job.appOrigin||''].join(':');
  if(displayedDraftKey!==key){
   displayedDraftKey=key;
   for(const name of ['company_bin','announcement_number','lot_number'])document.querySelector('#'+name).value=packet[name]||'';
   document.querySelector('#packet').textContent='Лот '+packet.lot_number+' · Объявление '+packet.announcement_number+(job.appOrigin?' · Получено из приложения':'');
   if(job.appOrigin)document.querySelector('#app-url').value=job.appOrigin;
  }
 }
 if(job?.number&&job.lotConfirmed&&job.status!=='running'){
  statusReport={schema:'goszakup-status-v1',company_bin:job.packet.company_bin,
   announcement_number:job.packet.announcement_number,lot_number:job.packet.lot_number,
   state:'draft',description_saved:job.status==='complete',number:job.number,url:'https://zakup.gov.kz/cabinet/purchase/my-applications/'+job.number,
   observed_at:job.observedAt||new Date().toISOString()};
  document.querySelector('#download').disabled=false;document.querySelector('#send').disabled=false;
  show(statusReport);
  if(['blocked','paused'].includes(job.status)){
   document.querySelector('#result-title').textContent='Черновик найден, заполнение остановлено';
   document.querySelector('#result-summary').textContent='Заявка № '+job.number+'. '+job.message;
   document.querySelector('#result-card').dataset.tone='warning';
  }else if(job.status==='complete'){
   document.querySelector('#result-title').textContent='Описание товара заполнено';
   document.querySelector('#result-summary').textContent='Заявка № '+job.number+'. '+job.message;
  }
 }
}
async function draftCommand(type){
 try{const result=await chrome.runtime.sendMessage({type});if(result.error)throw Error(result.error);renderDraft(result.job);}
 catch(error){show(error.message,'error');}
}
document.querySelector('#draft-start').onclick=()=>draftCommand('draft:start');
document.querySelector('#repeat-product').addEventListener('change',async()=>{
 if(!packet)return;
 packet={...packet,repeat_product:document.querySelector('#repeat-product').checked};
 try{const result=await chrome.runtime.sendMessage({type:'draft:load',packet});if(result.error)throw Error(result.error);renderDraft(result.job);}
 catch(error){show(error.message,'error');}
});
document.querySelector('#draft-stop').onclick=()=>draftCommand('draft:stop');
document.querySelector('#draft-clear').onclick=async()=>{
 await draftCommand('draft:clear');packet=null;statusReport=null;displayedDraftKey=undefined;
 document.querySelector('#file').value='';document.querySelector('#packet').textContent='';
 for(const id of ['fill','download','send'])document.querySelector('#'+id).disabled=true;
};
if(chrome.runtime?.sendMessage){
 void draftCommand('draft:get');
 chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.draftJob)renderDraft(changes.draftJob.newValue);});
}

let packet;
let statusReport;
const output=document.querySelector('#result');
const show=value=>output.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);
document.querySelector('#file').addEventListener('change',async event=>{
 packet=null;statusReport=null;document.querySelector('#status').disabled=true;document.querySelector('#download').disabled=true;document.querySelector('#fill').disabled=true;document.querySelector('#packet').textContent='';
 try {
  const file=event.target.files[0];if(!file)return;if(file.size>50000)throw Error('Слишком большой файл');
  const data=JSON.parse(await file.text());
  if(data.schema!=='goszakup-preparation-v1'||!/^\d{12}$/.test(data.company_bin)||!/^\d+$/.test(data.announcement_external_id)||typeof data.data!=='object'||!data.data)throw Error('Неверный формат данных');
  for(const key of ['product_name','manufacturer','country','contact_phone'])if(typeof data.data[key]!=='string'||data.data[key].length>1000)throw Error('Неверные поля');
  packet=data;document.querySelector('#packet').textContent='Объявление '+data.announcement_number+' · Лот '+data.lot_number+' · БИН '+data.company_bin;
  document.querySelector('#fill').disabled=false;document.querySelector('#status').disabled=false;show('Файл загружен только в память этого окна.');
 }catch(error){show(error.message);}
});
async function execute(mode){
 try {
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const url=new URL(tab.url);if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))throw Error('Откройте кабинет https://zakup.gov.kz/cabinet/');
  const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:inspectOrFill,args:[mode,mode==='fill'?packet:null]});
  show(results[0].result);
 }catch(error){show(error.message);}
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
 const bins=[...text.slice(0,1800).matchAll(/БИН\s*:?\s*(\d{12})/g)].map(m=>m[1]);
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
 statusReport=null;document.querySelector('#download').disabled=true;
 let validTarget=false,semanticFailure=false;
 try{
  if(!packet)throw Error('Загрузите данные подготовки');
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const url=new URL(tab.url);if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))throw Error('Откройте кабинет zakup.gov.kz');
  validTarget=true;
  const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readPortalStatus,args:[packet]});
  const report=results[0].result;if(report.error){semanticFailure=true;throw Error(report.error);}
  statusReport=report;document.querySelector('#download').disabled=false;show(report);
 }catch(error){
  if(validTarget&&!semanticFailure&&packet){statusReport={schema:'goszakup-status-v1',company_bin:packet.company_bin,announcement_number:packet.announcement_number,lot_number:packet.lot_number,observed_at:new Date().toISOString(),state:'unknown'};document.querySelector('#download').disabled=false;}
  show(error.message);
 }
};
document.querySelector('#download').onclick=()=>{
 if(!statusReport)return;
 const blob=new Blob([JSON.stringify(statusReport,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='goszakup-status.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};

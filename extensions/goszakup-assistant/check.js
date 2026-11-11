/* Only filters the public-facing supplier list. Never creates or changes an application. */
async function checkSavedApplication(target){
 const base={...target,observed_at:new Date().toISOString(),check_state:'unknown'};
 const visible=el=>Boolean(el?.getClientRects().length);
 const text=el=>el?.innerText||'';
 const pause=ms=>new Promise(r=>setTimeout(r,ms));
 const wait=async test=>{for(let i=0;i<100;i++){if(test())return;await pause(200);}throw Error('Страница или результаты поиска не загрузились.');};
 try{
  if(location.origin!=='https://zakup.gov.kz'||location.pathname!=='/cabinet/purchase/my-applications')throw Error('Нужен вход в «Мои заявки».');
  const ownBin=()=>[...new Set([...document.querySelectorAll('header,[role=banner],.ant-layout-header')].filter(visible).flatMap(el=>[...text(el).matchAll(/БИН\s*:?\s*(\d{12})/g)].map(m=>m[1])))];
  await wait(()=>ownBin().length);
  if(ownBin().length!==1||ownBin()[0]!==target.company_bin)throw Error('БИН выбранной организации не совпадает.');
  const inputs=()=>[...document.querySelectorAll('input')].filter(el=>visible(el)&&el.placeholder?.trim()==='№ объявления'&&!el.disabled);
  await wait(()=>inputs().length===1);
  const input=inputs()[0];
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,target.announcement_number);
  input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));
  const buttons=[...document.querySelectorAll('button')].filter(el=>visible(el)&&!el.disabled&&text(el).trim()==='Найти');
  if(buttons.length!==1)throw Error('Кнопка поиска не определена однозначно.');
  let changed=false,lastChange=Date.now();
  const observer=new MutationObserver(records=>{if(records.some(r=>r.target.closest?.('table,.ant-table,.ant-empty,.ant-spin'))){changed=true;lastChange=Date.now();}});
  observer.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['class','aria-busy']});
  try{
   buttons[0].click();
   await wait(()=>changed&&Date.now()-lastChange>800&&![...document.querySelectorAll('.ant-spin-spinning,[aria-busy=true]')].some(visible));
  }finally{observer.disconnect();}
  if(ownBin().length!==1||ownBin()[0]!==target.company_bin)throw Error('Организация изменилась во время проверки.');
  if(input.value!==target.announcement_number)throw Error('Фильтр объявления изменился.');
  if([...document.querySelectorAll('.ant-alert-error,.ant-notification-notice-error,.ant-message-error')].some(visible))throw Error('Портал сообщил об ошибке поиска.');
  const tables=[...document.querySelectorAll('table')].filter(visible);
  const rows=tables.flatMap(t=>[...t.querySelectorAll('tbody tr')]).filter(visible).filter(r=>!r.matches('.ant-table-measure-row,[aria-hidden=true]'));
  const matching=rows.filter(row=>{const cells=[...row.querySelectorAll(':scope > td')];return cells.some(c=>text(c).trim()===target.number)&&cells.some(c=>(text(c).match(/\d{6,}-\d+/g)||[]).includes(target.announcement_number));});
  if(matching.length===1){
   const status=[...matching[0].querySelectorAll(':scope > td')].map(c=>text(c).trim()).find(v=>['Проект','Подана','Подано','Заявка подана','Завершена','Отозвана'].includes(v));
   return {...base,check_state:'found',message:'Заявка №'+target.number+' найдена.'+(status?' Статус на портале: '+status: '')};
  }
  const hasHeaders=tables.some(t=>{const value=text(t.querySelector('thead')).replace(/\s+/g,' ');return value.includes('Статус заявки')&&value.includes('№ заявки');});
  const empty=[...document.querySelectorAll('.ant-empty-description,.ant-empty')].filter(visible).some(el=>/^Нет заявок$/.test(text(el).trim()));
  if(hasHeaders&&empty&&!rows.some(r=>[...r.querySelectorAll(':scope > td')].length>1&&text(r).trim()))return {...base,check_state:'absent',message:'По точному номеру объявления портал вернул «Нет заявок». Автоматическое создание не выполнялось.'};
  throw Error('Список не позволяет однозначно подтвердить наличие или отсутствие сохранённой заявки.');
 }catch(error){return {...base,message:error.message};}
}
if(typeof module!=='undefined')module.exports={checkSavedApplication};

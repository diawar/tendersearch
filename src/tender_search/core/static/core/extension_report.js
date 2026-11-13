(()=>{
 const message=document.querySelector('#transfer-result');
 const retry=document.querySelector('#transfer-retry');
 const fragment=location.hash.slice(1);
 // Report stays in this page's memory; never retain it in URL history or storage.
 history.replaceState(null,'',location.pathname+location.search);
 let report;
 try{
  if(!fragment||fragment.length>36000)throw Error();
  report=JSON.parse(decodeURIComponent(fragment));
  if(!report||!['goszakup-status-v1','goszakup-restriction-v1'].includes(report.schema))throw Error();
 }catch{
  message.textContent='Отчёт не получен. Войдите в приложение, вернитесь в расширение и нажмите «Передать в приложение».';
  return;
 }
 async function send(){
  if(!report||retry.disabled)return;
  retry.disabled=true;retry.hidden=true;message.textContent='Сохраняем отчёт в вашем аккаунте…';
  try{
   const response=await fetch(location.pathname,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('#transfer-form [name=csrfmiddlewaretoken]').value},body:JSON.stringify(report)});
   if(response.redirected)throw Error('Войдите в приложение и повторите передачу из расширения.');
   const data=await response.json();
   if(!response.ok||!data.saved)throw Error(data.error||'Не удалось сохранить отчёт.');
   const titles={draft:'Черновик создан',submitted:'Заявка подана',completed:'Заявка завершена на портале',unconfirmed:'Статус не подтверждён'};
   message.textContent='Отчёт сохранён. '+(titles[data.state]||'Статус не подтверждён')+(data.number?' · №'+data.number:'')+
    (data.description_saved?' · Описание товара заполнено':'')+
    (report.state==='submitted'&&data.stage==='waiting_results'?'. Этап: «Ожидаем итоги». Проверка по БИН продолжается.':'')+
    (report.state==='submitted'&&data.stage==='applied'?'. Этап: «Подали заявку». Проверка итогов включена.':'')+
    (report.state==='unknown'?'. Текущее состояние не подтверждено; сохранено последнее подтверждение.':'');
   if(report.schema==='goszakup-restriction-v1')message.textContent='Сохранено для лота '+report.lot_number+': изъятие из национального режима — '+(data.national_withdrawn?'Да':'Нет')+'. Этап работы и статус заявки не изменены.';
   document.querySelector('#transfer-lot').href=data.lot_url;
   document.querySelector('#transfer-preparation').href=data.preparation_url;
   document.querySelector('#transfer-links').hidden=false;
   report=null;
   retry.remove();
  }catch(error){message.textContent=error.message||'Приложение недоступно. Повторите передачу.';retry.disabled=false;retry.textContent='Повторить передачу';retry.hidden=false;}
 }
 const stateNames={draft:'Черновик',submitted:'Подана',completed:'Завершена',unknown:'Не подтверждена'};
 message.textContent='Объявление '+String(report.announcement_number||'')+' · лот '+String(report.lot_number||'')+
  ' · БИН '+String(report.company_bin||'')+' · статус: '+(stateNames[report.state]||'Неизвестен')+
  (report.description_saved===true?' · Описание товара заполнено':'')+
  (report.state==='submitted'?'. После сохранения лот перейдёт в «Подали заявку», если работа ещё не продвинулась дальше.':'')+'. Проверьте аккаунт и подтвердите сохранение.';
 retry.textContent='Сохранить статус в этом аккаунте';retry.hidden=false;
 if(report.schema==='goszakup-restriction-v1'){
  message.textContent='Лот '+report.lot_number+' · Изъятие из национального режима: '+(report.national_withdrawn?'Да':'Нет')+'. Подтвердите сохранение результата в приложении.';
  retry.textContent='Сохранить признак лота';
 }
 retry.addEventListener('click',send);
})();

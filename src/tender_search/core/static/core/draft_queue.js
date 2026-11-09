(()=>{
 const rows=[...document.querySelectorAll('[data-queue-id]')],output=document.querySelector('#queue-message');
 const csrf=document.querySelector('#queue-csrf [name=csrfmiddlewaretoken]').value;
 const channel='goszakup-preparation-bridge-v1';
 let extensionId,busy=false,polling=false,last='';
 const bridge=(type,extra={})=>new Promise((resolve,reject)=>{
  const id=crypto.randomUUID();let timer;
  const done=()=>{clearTimeout(timer);window.removeEventListener('message',listener);};
  const listener=event=>{
   const data=event.data;
   if(event.source!==window||event.origin!==location.origin||data?.channel!==channel||data.id!==id||data.type!==(type==='ping'?'ready':'result'))return;
   done();data.error?reject(Error(data.error)):resolve(data);
  };
  window.addEventListener('message',listener);
  timer=setTimeout(()=>{done();reject(Error('Расширение 0.9.0 не ответило. Обновите расширение и эту страницу в Chrome.'));},type==='ping'?1800:12000);
  window.postMessage({channel,id,type,extensionId,...extra},location.origin);
 });
 const connect=async()=>{if(!extensionId){const result=await bridge('ping');extensionId=result.extensionId;if(!extensionId)throw Error('Расширение не найдено.');}};
 const post=async(url,data,isForm=false)=>{
  const address=new URL(url,location.href);if(address.origin!==location.origin)throw Error('Неверный адрес очереди.');
  const response=await fetch(address.href,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':csrf,...(isForm?{}:{'Content-Type':'application/json'})},body:isForm?data:JSON.stringify(data)});
  if(response.redirected)throw Error('Войдите в приложение и откройте очередь заново.');
  const value=await response.json();if(!response.ok||value.error)throw Error(value.error||'Данные очереди недоступны.');return value;
 };
 async function poll(){
  if(polling||busy||!rows.length)return;polling=true;
  try{
   await connect();const result=await bridge('queue-status');
   if(!result.snapshot||!result.queue_context){
    for(const row of rows.filter(r=>['ready','running'].includes(r.dataset.state))){
     row.querySelector('.queue-state').textContent='Запуск не подтверждён браузером';
     row.querySelector('.queue-detail').textContent='Проверьте существующую заявку на Госзакупе перед продолжением. Последнее состояние сохранено в очереди.';
    }
    return;
   }
   const row=rows.find(r=>r.dataset.queueId===String(result.queue_context.id)&&r.dataset.generation===result.queue_context.generation);
   if(!row)return;
   const identity=JSON.stringify([result.queue_context,result.snapshot]);if(identity===last)return;
   const saved=await post(row.dataset.progressUrl,{generation:row.dataset.generation,snapshot:result.snapshot});
   last=identity;row.dataset.state=saved.state;row.querySelector('.queue-state').textContent=saved.label;
   row.querySelector('.queue-detail').textContent=result.snapshot.message||'';
   const remove=row.querySelector('.queue-remove');if(remove)remove.disabled=saved.state==='running';
   const start=row.querySelector('.queue-start');if(start)start.disabled=saved.state==='complete';
   output.textContent=saved.state==='complete'?'Черновик готов, результат сохранён в лоте. Можно передать следующий лот.':'Прогресс сохранён в очереди.';
  }catch(error){output.textContent=error.message||'Не удалось обновить очередь.';}
  finally{polling=false;}
 }
 for(const row of rows){
  const button=row.querySelector('.queue-start');
  button?.addEventListener('click',async()=>{
   if(busy||polling)return;busy=true;button.disabled=true;
   try{
    await connect();
    const current=await bridge('queue-status');
    if(current.snapshot?.status==='running')throw Error('Сначала завершите или остановите текущий лот в расширении.');
    const data=new FormData();if(row.querySelector('.queue-repeat')?.checked)data.set('repeat_product','on');
    const preparation=await post(row.dataset.packetUrl,data,true);
    const sent=await bridge('prepare',preparation);
    if(!sent.prepared)throw Error('Данные не переданы.');
    output.textContent='Объявление открыто. На его вкладке откройте расширение и нажмите «Создать / продолжить черновик». Эту страницу оставьте открытой.';
   }catch(error){output.textContent=error.message||'Не удалось передать лот.';}
   finally{busy=false;button.disabled=false;void poll();}
  });
 }
 void poll();setInterval(()=>void poll(),4000);
})();

(()=>{
 const button=document.querySelector('#prepare-with-extension');
 if(!button)return;
 const output=document.querySelector('#preparation-bridge-result');
 const form=document.querySelector('#preparation-form');
 const channel='goszakup-preparation-bridge-v1';
 let dirty=false;
 form?.addEventListener('input',()=>{dirty=true;});form?.addEventListener('change',()=>{dirty=true;});
 const request=(type,packet,timeout,extensionId)=>new Promise((resolve,reject)=>{
  const id=crypto.randomUUID();
  let timer;
  const done=()=>{clearTimeout(timer);window.removeEventListener('message',listener);};
  const listener=event=>{
   const data=event.data;
   if(event.source!==window||event.origin!==location.origin||!data||data.channel!==channel||data.id!==id||data.type!==(type==='ping'?'ready':'result'))return;
   done();data.error?reject(Error(data.error)):resolve(data);
  };
  window.addEventListener('message',listener);
  timer=setTimeout(()=>{done();reject(Error(type==='ping'?'Расширение 0.7.0 или новее не найдено на этой странице. Обновите расширение и перезагрузите страницу подготовки.':'Расширение не подтвердило передачу. Проверьте его окно перед повторной попыткой.'));},timeout);
  window.postMessage({channel,id,type,...(packet?{packet,extensionId}:{})},location.origin);
 });
 button.addEventListener('click',async()=>{
  if(button.disabled)return;
  button.disabled=true;
  try{
   if(dirty)throw Error('Сначала сохраните изменённые данные подготовки, затем передайте их в расширение.');
   output.textContent='Проверяем расширение…';
   const extension=await request('ping',null,1800);
   if(extension.draftPreflight!==true)throw Error('Обновите расширение до 0.18.0 для проверки существующих черновиков перед запуском.');
   if(typeof extension.extensionId!=='string'||!extension.extensionId)throw Error('Расширение не подтвердило соединение.');
   const url=new URL(button.dataset.exportUrl,location.href);
   if(url.origin!==location.origin)throw Error('Неверный адрес подготовки.');
   const response=await fetch(url.href,{credentials:'same-origin',cache:'no-store'});
   if(response.redirected||!response.ok||!response.headers.get('Content-Type')?.includes('application/json'))throw Error('Не удалось получить подготовку. Проверьте вход, сохранённые данные и БИН организации.');
   const packet=await response.json();
   if(dirty)throw Error('Данные изменились. Сначала сохраните подготовку.');
   output.textContent='Передаём подготовку в расширение…';
   const result=await request('prepare',packet,12000,extension.extensionId);
   output.textContent=result.opened?'Данные переданы, объявление открыто в новой вкладке. На Госзакупе откройте расширение и нажмите «Создать / продолжить черновик».':'Данные переданы. Откройте объявление Госзакупа и запустите заполнение в расширении.';
  }catch(error){output.textContent=error.message||'Не удалось передать подготовку.';}
  finally{button.disabled=false;document.dispatchEvent(new Event('preparation-launch-idle'));}
 });
})();

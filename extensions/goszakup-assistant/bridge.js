/* One-way preparation handoff from our application, never a portal controller. */
(()=>{
 const queuePage=location.pathname==='/workspace/drafts/';
 if(window.top!==window||!globalThis.GOSZAKUP_APP_ORIGINS?.includes(location.origin)||(!/^\/lots\/\d+\/preparation\/$/.test(location.pathname)&&!queuePage))return;
 const channel='goszakup-preparation-bridge-v1';
 let busy=false;
 const reply=(id,value)=>window.postMessage({channel,id,...value},location.origin);
 window.addEventListener('message',async event=>{
  const message=event.data;
  if(event.source!==window||event.origin!==location.origin||!message||message.channel!==channel||typeof message.id!=='string'||!/^[\w-]{16,80}$/.test(message.id))return;
  if(message.type==='ping'){reply(message.id,{type:'ready',extensionId:chrome.runtime.id});return;}
  if(message.type!=='prepare'&&!(queuePage&&message.type==='queue-status'))return;
  if(message.extensionId!==chrome.runtime.id)return;
  if(busy){reply(message.id,{type:'result',error:'Передача уже выполняется.'});return;}
  busy=true;
  try{
   if(message.type==='queue-status'){
    const result=await chrome.runtime.sendMessage({type:'bridge:queue-status'});
    reply(message.id,{type:'result',...result});return;
   }
   if(JSON.stringify(message.packet).length>50000)throw Error('Слишком большой пакет подготовки.');
   const result=await chrome.runtime.sendMessage({type:'bridge:prepare',packet:message.packet,...(queuePage?{queue_context:message.queue_context}:{})});
   if(result?.error)throw Error(result.error);
   if(!result?.prepared)throw Error('Расширение не подтвердило получение данных.');
   reply(message.id,{type:'result',prepared:true,opened:result.opened===true});
  }catch(error){reply(message.id,{type:'result',error:error.message||'Не удалось передать подготовку.'});}
  finally{busy=false;}
 });
})();

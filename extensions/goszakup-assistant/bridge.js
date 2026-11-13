/* One-way preparation handoff from our application, never a portal controller. */
(()=>{
 const queuePage=location.pathname==='/workspace/drafts/';
 const preparationPage=/^\/lots\/\d+\/preparation\/$/.test(location.pathname);
 if(window.top!==window||!globalThis.GOSZAKUP_APP_ORIGINS?.includes(location.origin))return;
 if(globalThis.GOSZAKUP_BRIDGE_ATTACHED)return;
 globalThis.GOSZAKUP_BRIDGE_ATTACHED=true;
 const channel='goszakup-preparation-bridge-v1';
 let busy=false;
 const reply=(id,value)=>window.postMessage({channel,id,...value},location.origin);
 async function account(){
  const response=await fetch('/account/session/',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});
  if(!response.ok||response.redirected)throw Error('Не удалось проверить аккаунт приложения.');
  const value=await response.json();if(typeof value.authenticated!=='boolean'||(value.authenticated&&(!/^[a-f0-9]{40}$/.test(value.scope)||!/^[a-f0-9]{40}$/.test(value.account))))throw Error('Обновите страницу приложения.');
  return {...value,origin:location.origin};
 }
 async function authorize(){const value=await account();await chrome.runtime.sendMessage({type:'bridge:account',account:value});
  if(!value.authenticated||!document.body?.dataset.accountScope||value.scope!==document.body.dataset.accountScope)throw Error('Учётная запись изменилась. Обновите страницу приложения.');return value;}
 async function sync(){try{await chrome.runtime.sendMessage({type:'bridge:account',account:await account()});}catch(_error){}}
 chrome.runtime.onMessage.addListener((message,sender,respond)=>{if(sender.id!==chrome.runtime.id||message?.type!=='account:verify')return false;account().then(respond,()=>respond({error:true}));return true;});
 window.addEventListener('focus',sync);document.addEventListener('visibilitychange',()=>{if(!document.hidden)void sync();});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',sync,{once:true});else void sync();
 window.addEventListener('message',async event=>{
  const message=event.data;
  if(event.source!==window||event.origin!==location.origin||!message||message.channel!==channel||typeof message.id!=='string'||!/^[\w-]{16,80}$/.test(message.id))return;
  if(!queuePage&&!preparationPage)return;
  if(message.type==='ping'){try{await authorize();reply(message.id,{type:'ready',extensionId:chrome.runtime.id,autoQueue:true,queueCheck:true,queueReset:true,draftPreflight:true});}catch(error){reply(message.id,{type:'ready',error:error.message});}return;}
  if(message.type!=='prepare'&&!(queuePage&&['queue-status','queue-stop','queue-check'].includes(message.type)))return;
  if(message.extensionId!==chrome.runtime.id)return;
  if(busy){reply(message.id,{type:'result',error:'Передача уже выполняется.'});return;}
  busy=true;
  try{
   const binding=await authorize();
   if(['queue-status','queue-stop','queue-check'].includes(message.type)){
    const result=await chrome.runtime.sendMessage({type:'bridge:'+message.type,account:binding,...(message.type==='queue-check'?{target:message.target}:{})});
    reply(message.id,{type:'result',...result});return;
   }
   if(JSON.stringify(message.packet).length>50000)throw Error('Слишком большой пакет подготовки.');
   const result=await chrome.runtime.sendMessage({type:'bridge:prepare',account:binding,packet:message.packet,...(queuePage?{queue_context:message.queue_context,auto_start:message.auto_start===true}:{})});
   if(result?.error)throw Error(result.error);
   if(!result?.prepared)throw Error('Расширение не подтвердило получение данных.');
   reply(message.id,{type:'result',prepared:true,opened:result.opened===true});
  }catch(error){reply(message.id,{type:'result',error:error.message||'Не удалось передать подготовку.'});}
  finally{busy=false;}
 });
})();

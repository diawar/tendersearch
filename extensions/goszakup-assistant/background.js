importScripts('draft.js','bridge-config.js');
let running=false;
let transferring=false;
const key='draftJob';
const readJob=async()=> (await chrome.storage.session.get(key))[key]||null;
const recoveryKey=packet=>'draftRecovery:'+packet.company_bin+':'+packet.announcement_number+':'+packet.lot_number;
async function recovery(packet){
 const record=(await chrome.storage.local.get(recoveryKey(packet)))[recoveryKey(packet)];
 const number=record?.number||packet.existing_application_number||'';
 if(number&&!/^\d+$/.test(number))throw Error('Некорректный сохранённый номер черновика.');
 return {number,createUncertain:record?.createUncertain===true};
}
const writeJob=async job=>{
 // Persist the minimal duplicate guard before an action, without product or bank data.
 if(job.number||job.createUncertain)await chrome.storage.local.set({[recoveryKey(job.packet)]:{number:job.number||'',createUncertain:job.createUncertain===true}});
 else if(job.createUncertain===false)await chrome.storage.local.remove(recoveryKey(job.packet));
 await chrome.storage.session.set({[key]:job});return job;
};
async function injected(job,action){
 const rows=await chrome.scripting.executeScript({target:{tabId:job.tabId},func:runDraftStep,
  args:[job.packet,action,{applicationNumber:job.number||'',lotConfirmed:job.lotConfirmed===true}]});
 if(rows.length!==1||!rows[0].result)throw Error('Ответ страницы не получен. Проверьте черновик на портале.');
 return rows[0].result;
}
async function navigatePortal(job,address){
 const url=new URL(address),path='/cabinet/purchase/announcements/'+job.packet.announcement_external_id+'/apply';
 const step=Number(url.searchParams.get('step')||'1'),id=url.searchParams.get('id');
 if(url.origin!=='https://zakup.gov.kz'||url.pathname!==path||url.username||url.password||url.hash||
   [...url.searchParams.keys()].some(key=>!['id','step'].includes(key))||url.searchParams.getAll('id').length>1||url.searchParams.getAll('step').length>1||
   !Number.isInteger(step)||step<1||step>3||(id!==null&&!/^\d+$/.test(id))||job.number&&id!==job.number)throw Error('Адрес перехода к черновику не подтверждён.');
 await new Promise((resolve,reject)=>{
  let timer;
  const done=error=>{clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);error?reject(error):resolve();};
  const listener=(tabId,change)=>{if(tabId===job.tabId&&change.status==='complete')done();};
  chrome.tabs.onUpdated.addListener(listener);
  timer=setTimeout(()=>done(Error('Переход ещё не завершился. Проверьте открытую страницу и продолжите после загрузки.')),20000);
  chrome.tabs.update(job.tabId,{url:url.href}).catch(done);
 });
}
async function runJob(){
 try{
  for(let step=0;step<24;step++){
   let job=await readJob();if(!job||job.stop) return;
   const probe=await injected(job,'probe');
   if(probe.blocked){await writeJob({...job,status:'blocked',message:probe.message});return;}
   if((job.createUncertain||job.number)&&!probe.number&&probe.next==='save_account'){
    await writeJob({...job,status:'blocked',message:'Предыдущая попытка создания не подтверждена. Откройте объявление заново и продолжите существующую заявку; повторное создание отключено.'});return;
   }
   if(probe.number){job.number=probe.number;job.createUncertain=false;}
   if(probe.lotConfirmed)job.lotConfirmed=true;
   if(probe.state==='complete'){
    if(!job.number||!job.lotConfirmed)throw Error('Номер черновика и состав заявки не подтверждены.');
    await writeJob({...job,status:'complete',message:probe.message,observedAt:new Date().toISOString(),intent:''});return;
   }
   if(!probe.next)throw Error('Не определён следующий шаг. Продолжите вручную.');
   const current=await readJob();if(current?.stop)return;
   job={...job,status:'running',intent:probe.next,message:probe.message,
    createUncertain:job.createUncertain||(probe.next==='save_account'&&!job.number)};
   await writeJob(job); // Durable intent before any portal action.
   const result=await injected(job,probe.next);
   // A confirmed preflight refusal means no create click happened. Transport
   // failures keep the durable uncertainty marker, even if the UI looks unchanged.
   if(probe.next==='save_account'&&!job.number&&result.blocked&&result.uncertain===false)job.createUncertain=false;
   if(result.number){job.number=result.number;job.createUncertain=false;}
   if(result.lotConfirmed)job.lotConfirmed=true;
   if(result.navigate){
    job={...job,status:'running',message:result.message,intent:'open_form'};await writeJob(job);
    await navigatePortal(job,result.navigate);
    const current=await readJob();if(current?.stop)return;
   }
   const stopped=(await readJob())?.stop;
   job={...job,stop:Boolean(stopped),status:stopped?'paused':result.blocked?'blocked':result.state==='complete'?'complete':'running',
    message:result.message,intent:result.blocked?job.intent:'',observedAt:new Date().toISOString()};
   await writeJob(job);
   if(result.blocked||result.state==='complete'||stopped)return;
  }
  const job=await readJob();if(job)await writeJob({...job,status:'paused',message:'Достигнут предел шагов. Проверьте форму и продолжите.'});
 }catch(error){
  const job=await readJob();if(job)await writeJob({...job,status:'blocked',message:error.message});
 }finally{running=false;}
}
async function command(message){
 if(transferring&&['draft:load','draft:start','draft:clear'].includes(message.type))throw Error('Дождитесь завершения передачи из приложения.');
 if(message.type==='draft:get'){
  const job=await readJob();
  if(job?.status==='running'&&!running)return writeJob({...job,status:'paused',message:'Фоновая обработка прервалась. Проверьте портал перед продолжением.'});
  return job;
 }
 if(message.type==='draft:load'){
  if(running)throw Error('Сначала остановите текущее заполнение.');
  validateDraftPacket(message.packet);
  const old=await readJob();
  if(old?.createUncertain)throw Error('Есть неподтверждённая попытка создания. Проверьте её на портале перед очисткой подготовки.');
  return writeJob({packet:message.packet,status:'ready',message:'Подготовка загружена. Откройте нужное объявление.',...await recovery(message.packet),lotConfirmed:false});
 }
 if(message.type==='draft:stop'){
  const job=await readJob();return job?writeJob({...job,stop:true,status:'paused',message:'Остановка запрошена. Текущий шаг может завершиться; следующие шаги отключены.'}):null;
 }
 if(message.type==='draft:clear'){
  if(running)throw Error('Дождитесь остановки текущего шага.');
  await chrome.storage.session.remove(key);return null;
 }
 if(message.type==='draft:start'){
  if(running)throw Error('Заполнение уже выполняется.');
  let job=await readJob();validateDraftPacket(job?.packet);
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const url=new URL(tab?.url||'');
  if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))throw Error('Откройте нужное объявление в кабинете zakup.gov.kz.');
  running=true;
  try{job=await writeJob({...job,tabId:tab.id,stop:false,status:'running',message:'Проверяем форму…'});}
  catch(error){running=false;throw error;}
  void runJob();return job;
 }
 throw Error('Неизвестная команда.');
}
function trustedPreparationSender(sender){
 try{
  const url=new URL(sender.url);
  return sender.id===chrome.runtime.id&&sender.frameId===0&&Number.isInteger(sender.tab?.id)&&
   globalThis.GOSZAKUP_APP_ORIGINS?.includes(url.origin)&&(/^\/lots\/\d+\/preparation\/$/.test(url.pathname)||url.pathname==='/workspace/drafts/');
 }catch{return false;}
}
async function receivePreparation(message,sender){
 if(running||transferring)throw Error('Сначала завершите или остановите текущее заполнение в расширении.');
 transferring=true;
 try{
  if(JSON.stringify(message.packet).length>50000)throw Error('Слишком большой пакет подготовки.');
  validateDraftPacket(message.packet);
  const old=await readJob();
  if(old?.createUncertain)throw Error('Проверьте на портале предыдущую неподтверждённую попытку создания черновика.');
  const packet=message.packet;
  const queue=message.queue_context;
  if(queue&&(!Number.isSafeInteger(queue.id)||queue.id<1||!/^[-a-f0-9]{36}$/.test(queue.generation)||new URL(sender.url).pathname!=='/workspace/drafts/'))throw Error('Некорректный пункт очереди.');
  const same=old?.packet?.company_bin===packet.company_bin&&old?.packet?.announcement_number===packet.announcement_number&&old?.packet?.lot_number===packet.lot_number;
  await writeJob({packet,status:'ready',message:'Подготовка получена из приложения. Нажмите «Создать / продолжить черновик» на вкладке Госзакупа.',
   ...await recovery(packet),number:(same?old.number:'')||(await recovery(packet)).number,lotConfirmed:same&&old.lotConfirmed===true,appOrigin:new URL(sender.url).origin,queue_context:queue||null});
  let opened=false;
  try{await chrome.tabs.create({url:'https://zakup.gov.kz/cabinet/announcement/'+packet.announcement_external_id});opened=true;}catch{}
  try{await chrome.action.setBadgeText({text:'1'});await chrome.action.setBadgeBackgroundColor({color:'#087ee5'});}catch{}
  return {prepared:true,opened};
 }finally{transferring=false;}
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(message?.type==='bridge:queue-status'){
  if(!trustedPreparationSender(sender)||new URL(sender.url).pathname!=='/workspace/drafts/')return false;
  command({type:'draft:get'}).then(job=>{
   if(!job?.queue_context||job.appOrigin!==new URL(sender.url).origin){respond({snapshot:null});return;}
   respond({queue_context:job.queue_context,snapshot:{status:job.status,number:job.number||'',message:job.message||'',
    company_bin:job.packet.company_bin,announcement_number:job.packet.announcement_number,lot_number:job.packet.lot_number,
    lotConfirmed:job.lotConfirmed===true,description_saved:job.status==='complete',observed_at:job.observedAt||null}});
  },error=>respond({error:error.message}));return true;
 }
 if(message?.type==='bridge:prepare'){
  if(!trustedPreparationSender(sender))return false;
  receivePreparation(message,sender).then(respond,error=>respond({error:error.message}));return true;
 }
 if(sender.id!==chrome.runtime.id||sender.url!==chrome.runtime.getURL('popup.html'))return false;
 if(message.type==='draft:get')void chrome.action?.setBadgeText({text:''}).catch(()=>{});
 command(message).then(job=>respond({job}),error=>respond({error:error.message}));return true;
});

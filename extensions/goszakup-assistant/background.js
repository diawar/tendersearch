importScripts('draft.js','bridge-config.js','check.js');
let running=false;
let transferring=false;
const key='draftJob';
const readJob=async()=> (await chrome.storage.session.get(key))[key]||null;
const accountsKey='appAccounts';
const readAccounts=async()=> (await chrome.storage.session.get(accountsKey))[accountsKey]||{};
const accountRevisions=new Map();
function trustedAccountSender(sender){try{return sender.id===chrome.runtime.id&&sender.frameId===0&&Number.isInteger(sender.tab?.id)&&globalThis.GOSZAKUP_APP_ORIGINS?.includes(new URL(sender.url).origin);}catch{return false;}}
async function clearPrivateJob(){await chrome.alarms.clear('draft-recovery');await chrome.storage.session.remove(key);}
async function bindAccount(account,sender,revision){
 const origin=new URL(sender.url).origin;
 const unchanged=()=>{if(accountRevisions.get(origin)!==revision)throw Error('Учётная запись проверяется заново. Повторите действие.');};
 unchanged();
 if(!account||account.origin!==origin||typeof account.authenticated!=='boolean'||(account.authenticated&&(!/^[a-f0-9]{40}$/.test(account.scope)||!/^[a-f0-9]{40}$/.test(account.account))))throw Error('Аккаунт приложения не подтверждён.');
 const accounts=await readAccounts(),next={scope:account.authenticated?account.scope:'',account:account.authenticated?account.account:'',tabId:sender.tab.id};
 const job=await readJob();unchanged();if(job&&(!job.accountScope||job.appOrigin===origin&&job.accountScope!==next.scope))await clearPrivateJob();
 unchanged();if(JSON.stringify(accounts[origin])!==JSON.stringify(next)){accounts[origin]=next;await chrome.storage.session.set({[accountsKey]:accounts});}
 unchanged();
 return {...next,revision};
}
async function freshAccount(sender){
 const origin=new URL(sender.url).origin,revision=(accountRevisions.get(origin)||0)+1;accountRevisions.set(origin,revision);
 const current=await chrome.tabs.sendMessage(sender.tab.id,{type:'account:verify'});
 if(current?.error)throw Error('Не удалось проверить аккаунт приложения.');
 return bindAccount(current,sender,revision);
}
async function verifyBoundJob(job){
 if(!job)return false;
 if(!job.accountScope||!job.appOrigin){await clearPrivateJob();return false;}
 const bound=(await readAccounts())[job.appOrigin];
 if(!bound||bound.scope!==job.accountScope){await clearPrivateJob();return false;}
 const current=await freshAccount({url:job.appOrigin,id:chrome.runtime.id,frameId:0,tab:{id:bound.tabId}});
 const latest=await readJob(),binding=(await readAccounts())[job.appOrigin];
 return current.revision===accountRevisions.get(job.appOrigin)&&current.scope===job.accountScope&&binding?.scope===job.accountScope&&latest?.accountScope===job.accountScope;
}
async function authorizeSender(message,sender){
 const account=await freshAccount(sender);
 if(!account.scope||message.account?.scope!==account.scope)throw Error('Учётная запись изменилась. Обновите страницу приложения.');
 return account;
}
const legacyRecoveryKey=packet=>'draftRecovery:'+packet.company_bin+':'+packet.announcement_number+':'+packet.lot_number;
const recoveryKey=packet=>packet.app_account?'draftRecovery:'+packet.app_account+':'+legacyRecoveryKey(packet):legacyRecoveryKey(packet);
async function recovery(packet){
 const record=(await chrome.storage.local.get(recoveryKey(packet)))[recoveryKey(packet)];
 if(!record&&packet.app_account&&(await chrome.storage.local.get(legacyRecoveryKey(packet)))[legacyRecoveryKey(packet)])return {number:'',createUncertain:true};
 const number=record?.number||packet.existing_application_number||'';
 if(number&&!/^\d+$/.test(number))throw Error('Некорректный сохранённый номер черновика.');
 return {number,createUncertain:record?.createUncertain===true};
}
const writeJob=async job=>{
 if(job.accountScope&&(await readAccounts())[job.appOrigin]?.scope!==job.accountScope)throw Error('Учётная запись изменилась. Заполнение остановлено.');
 // Persist the minimal duplicate guard before an action, without product or bank data.
 if(job.number||job.createUncertain)await chrome.storage.local.set({[recoveryKey(job.packet)]:{number:job.number||'',createUncertain:job.createUncertain===true}});
 else if(job.createUncertain===false)await chrome.storage.local.remove(recoveryKey(job.packet));
 if(job.accountScope&&(await readAccounts())[job.appOrigin]?.scope!==job.accountScope)throw Error('Учётная запись изменилась. Заполнение остановлено.');
 await chrome.storage.session.set({[key]:job});
 if(job.accountScope&&(await readAccounts())[job.appOrigin]?.scope!==job.accountScope){if((await readJob())?.accountScope===job.accountScope)await clearPrivateJob();throw Error('Учётная запись изменилась. Заполнение остановлено.');}
 return job;
};
const retryAlarm='draft-recovery';
const retryDelays=[1,2,4,8,15,15,15,15];
async function waitRecovery(job,reason){
 const current=await readJob();if(!current||current.stop)return;
 const attempt=job.retryCount||0;
 if(attempt>=retryDelays.length){await writeJob({...job,status:'blocked',message:'Предел автоматических повторов достигнут. '+reason});return;}
 const nextRetryAt=Date.now()+retryDelays[attempt]*60000;
 await writeJob({...job,status:'waiting',retryCount:attempt+1,nextRetryAt,
  message:'Ожидание восстановления. Проверка '+(attempt+1)+'/'+retryDelays.length+' через '+retryDelays[attempt]+' мин. '+reason});
 await chrome.alarms.create(retryAlarm,{when:nextRetryAt});
 if((await readJob())?.stop)await chrome.alarms.clear(retryAlarm);
}
async function resumeRecovery(){
 if(running||transferring)return;
 const job=await readJob();if(!job||job.stop||job.status!=='waiting')return;
 running=true;
 try{
  if(!await verifyBoundJob(job))return;
  await writeJob({...job,status:'running',message:'Проверяем восстановление и существующий черновик…'});
  await new Promise((resolve,reject)=>{
   let timer;
   const done=error=>{clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);error?reject(error):resolve();};
   const listener=(id,change)=>{if(id===job.tabId&&change.status==='complete')done();};
   chrome.tabs.onUpdated.addListener(listener);
   timer=setTimeout(()=>done(Error('Страница Госзакупа не загрузилась.')),30000);
   chrome.tabs.update(job.tabId,{url:'https://zakup.gov.kz/cabinet/announcement/'+job.packet.announcement_external_id}).catch(done);
  });
  await runJob();
 }catch(error){await waitRecovery(await readJob(),error.message);}
 finally{running=false;}
}
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name===retryAlarm)void resumeRecovery();});
async function injected(job,action){
 if(!await verifyBoundJob(job))throw Error('Учётная запись изменилась. Заполнение остановлено.');
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
async function loadPortalTab(tabId,url){
 await chrome.tabs.update(tabId,{url});
 for(let n=0;n<150;n++){const tab=await chrome.tabs.get(tabId);if(tab.status==='complete')return;await new Promise(resolve=>setTimeout(resolve,200));}
 throw Error('Страница проверки не загрузилась. Новый черновик не создаётся.');
}
async function preflightDraft(){
 let job=await readJob();if(!job||job.stop||!await verifyBoundJob(job))return false;
 await writeJob({...job,status:'running',phase:'portal',message:'Проверяем «Мои заявки» перед запуском. Создание нового черновика пока запрещено.'});
 let tab;
 try{
  tab=await chrome.tabs.create({active:false,url:'https://zakup.gov.kz/cabinet/purchase/my-applications'});
  for(let n=0;n<150;n++){if((await chrome.tabs.get(tab.id)).status==='complete')break;if(n===149)throw Error('Кабинет не загрузился. Проверка не выполнена.');await new Promise(resolve=>setTimeout(resolve,200));}
  if(!await verifyBoundJob(job))return false;
  const target={company_bin:job.packet.company_bin,announcement_number:job.packet.announcement_number,lot_number:job.packet.lot_number,number:job.number||job.packet.existing_application_number||''};
  const rows=await chrome.scripting.executeScript({target:{tabId:tab.id},func:checkSavedApplication,args:[target]});
  if(!await verifyBoundJob(job))return false;
  const current=await readJob();if(!current||current.stop)return false;job=current;
  const decision=draftPreflightDecision(job,rows.length===1?rows[0].result:null);
  if(decision.action==='block'){await writeJob({...job,status:'blocked',message:decision.message});return false;}
  if(decision.action==='wait'){await waitRecovery({...job,absenceConfirmedAt:decision.confirmAbsence?(job.absenceConfirmedAt||Date.now()):null},decision.message);return false;}
  const number=decision.action==='resume'?decision.number:'';
  job={...job,number,packet:{...job.packet,existing_application_number:number},createUncertain:false,absenceConfirmedAt:null,
   lotConfirmed:decision.action==='resume'&&job.number===number&&job.lotConfirmed===true,
   message:number?'Найден черновик № '+number+'. Продолжаем его; новый не создаём.':'Портал подтвердил отсутствие заявок по объявлению. Продолжаем создание черновика.'};
  await writeJob(job);
  if(!await verifyBoundJob(job)||(await readJob())?.stop)return false;
  await loadPortalTab(job.tabId,'https://zakup.gov.kz/cabinet/announcement/'+job.packet.announcement_external_id);
  return true;
 }catch(error){job=await readJob();if(job&&!job.stop){const message='Проверка перед запуском не выполнена. Новый черновик не создаётся. '+error.message;if(/не загрузил|Frame.*removed|document.*unloaded|Cannot access contents|network|net::/i.test(error.message))await waitRecovery({...job,absenceConfirmedAt:null},message);else await writeJob({...job,status:'blocked',message});}return false;}
 finally{if(tab)try{await chrome.tabs.remove(tab.id);}catch{}}
}
async function runJob(){
 try{
  if(!await preflightDraft())return;
  for(let step=0;step<24;step++){
   let job=await readJob();if(!job||job.stop) return;
   const probe=await injected(job,'probe');
   if((await readJob())?.stop)return;
   if(probe.blocked){if(probe.retryable)await waitRecovery(job,probe.message);else await writeJob({...job,status:'blocked',message:probe.message});return;}
   if(job.createUncertain&&!probe.number&&probe.next==='open_form'&&!probe.resuming){await waitRecovery(job,'Результат создания неизвестен. Проверяем существующий черновик; повторное создание запрещено.');return;}
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
   job={...job,status:'running',intent:probe.next,phase:({open_form:'portal',fill_account:'account',save_account:'account',add_lot:'lot',advance_lots:'lot',open_description:'description',edit_description:'description',fill_description:'description',save_description:'description'})[probe.next]||job.phase||'portal',message:probe.message,
    createUncertain:job.createUncertain||(probe.next==='save_account'&&!job.number),absenceConfirmedAt:null};
   await writeJob(job); // Durable intent before any portal action.
   const result=await injected(job,probe.next);
   // A confirmed preflight refusal means no create click happened. Transport
   // failures keep the durable uncertainty marker, even if the UI looks unchanged.
   if(probe.next==='save_account'&&!job.number&&result.blocked&&result.uncertain===false)job.createUncertain=false;
   if(result.number){job.number=result.number;job.createUncertain=false;}
   if(result.lotConfirmed)job.lotConfirmed=true;
   if(result.state==='draft_created')job.phase='lot';
   if(['documents_opened','description_opened','description_ready'].includes(result.state))job.phase='description';
   if(result.navigate){
    job={...job,status:'running',message:result.message,intent:'open_form'};await writeJob(job);
    await navigatePortal(job,result.navigate);
    const current=await readJob();if(current?.stop)return;
   }
   const stopped=(await readJob())?.stop;
   if(!stopped&&result.blocked&&result.retryable){await waitRecovery(job,result.message);return;}
   job={...job,stop:Boolean(stopped),status:stopped?'paused':result.blocked?'blocked':result.state==='complete'?'complete':'running',
    message:result.message,intent:result.blocked?job.intent:'',observedAt:new Date().toISOString()};
   await writeJob(job);
   if(result.blocked||result.state==='complete'||stopped)return;
  }
  const job=await readJob();if(job)await writeJob({...job,status:'paused',message:'Достигнут предел шагов. Проверьте форму и продолжите.'});
 }catch(error){
  const job=await readJob();if(job&&!job.stop){if(/Frame.*removed|document.*unloaded|message port closed|Cannot access contents|Переход ещё не завершился|Ответ страницы не получен/i.test(error.message))await waitRecovery(job,error.message);else await writeJob({...job,status:'blocked',message:error.message});}
 }finally{running=false;}
}
async function command(message){
 if(!['draft:get','draft:load'].includes(message.type)){const job=await readJob();if(job&&!await verifyBoundJob(job))return null;}
 if(transferring&&['draft:load','draft:start','draft:clear'].includes(message.type))throw Error('Дождитесь завершения передачи из приложения.');
 if(message.type==='draft:get'){
  const job=await readJob();
  if(job&&!await verifyBoundJob(job))return null;
  if(job?.status==='running'&&!running){await waitRecovery(job,'Обработка прервалась. Перед продолжением проверим существующий черновик.');return readJob();}
  if(job?.status==='waiting'&&job.nextRetryAt)await chrome.alarms.create(retryAlarm,{when:Math.max(Date.now()+1000,job.nextRetryAt)});
  return job;
 }
 if(message.type==='draft:load'){
  throw Error('Передайте подготовку из текущего аккаунта приложения.');
 }
 if(message.type==='draft:stop'){
  await chrome.alarms.clear(retryAlarm);
  const job=await readJob();return job?writeJob({...job,stop:true,status:'paused',message:'Остановка запрошена. Текущий шаг может завершиться; следующие шаги отключены.'}):null;
 }
 if(message.type==='draft:confirm-absent'){
  if(running||transferring)throw Error('Сначала остановите заполнение и дождитесь завершения текущего шага.');
  const job=await readJob();
  if(!job||!['paused','blocked','ready'].includes(job.status)||!job.createUncertain||job.number||job.packet.existing_application_number)throw Error('Сначала остановите ожидание. Сброс доступен только для неподтверждённой попытки без номера заявки.');
  if(message.announcement_number!==job.packet.announcement_number||message.lot_number!==job.packet.lot_number||message.company_bin!==job.packet.company_bin)throw Error('Подготовка изменилась. Откройте расширение заново.');
  await chrome.alarms.clear(retryAlarm);
  return writeJob({...job,createUncertain:false,retryCount:0,nextRetryAt:null,intent:'',message:'Вы подтвердили отсутствие черновика в «Моих заявках». Вернитесь в очередь и нажмите «Запустить очередь».'});
 }
 if(message.type==='draft:clear'){
  if(running)throw Error('Дождитесь остановки текущего шага.');
  await chrome.alarms.clear(retryAlarm);
  await chrome.storage.session.remove(key);return null;
 }
 if(message.type==='draft:start'){
  if(running||(await readJob())?.status==='waiting')throw Error('Заполнение или ожидание уже выполняется.');
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
 const account=await authorizeSender(message,sender);
 if(running||transferring)throw Error('Сначала завершите или остановите текущее заполнение в расширении.');
 transferring=true;
 try{
  if((await readJob())?.status==='waiting')throw Error('Сначала остановите ожидание восстановления.');
  if(JSON.stringify(message.packet).length>50000)throw Error('Слишком большой пакет подготовки.');
  validateDraftPacket(message.packet);
  const old=await readJob();
  if(old?.createUncertain&&(old.packet.company_bin!==message.packet.company_bin||old.packet.lot_number!==message.packet.lot_number||old.packet.announcement_number!==message.packet.announcement_number))throw Error('Проверьте на портале предыдущую неподтверждённую попытку создания черновика.');
  const packet={...message.packet,app_account:account.account};
  const queue=message.queue_context;
  if(message.auto_start===true&&!queue)throw Error('Автозапуск доступен только из очереди.');
  if(queue&&(!Number.isSafeInteger(queue.id)||queue.id<1||!/^[-a-f0-9]{36}$/.test(queue.generation)||new URL(sender.url).pathname!=='/workspace/drafts/'))throw Error('Некорректный пункт очереди.');
  if(packet.reset_absent_application!==undefined){
   if(!queue||!/^\d{1,30}$/.test(packet.reset_absent_application)||packet.existing_application_number)throw Error('Сброс старого номера не подтверждён очередью.');
   const record=await recovery(packet);
   if(record.createUncertain)throw Error('Есть неподтверждённая попытка создания. Проверьте её отдельно.');
   if(record.number===packet.reset_absent_application)await chrome.storage.local.remove(recoveryKey(packet));
  }
  const same=old?.accountScope===account.scope&&!packet.reset_absent_application&&old?.packet?.company_bin===packet.company_bin&&old?.packet?.announcement_number===packet.announcement_number&&old?.packet?.lot_number===packet.lot_number;
  await writeJob({packet,accountScope:account.scope,status:'ready',message:'Подготовка получена из приложения. Нажмите «Создать / продолжить черновик» на вкладке Госзакупа.',
   ...await recovery(packet),number:(same?old.number:'')||(await recovery(packet)).number,lotConfirmed:same&&old.lotConfirmed===true,appOrigin:new URL(sender.url).origin,queue_context:queue||null});
  let opened=false;
  try{
   const tab=await chrome.tabs.create({url:'https://zakup.gov.kz/cabinet/announcement/'+packet.announcement_external_id});opened=true;
   if(message.auto_start===true){
    running=true;
    await writeJob({...await readJob(),tabId:tab.id,stop:false,status:'running',message:'Открываем объявление…'});
    void (async()=>{
     try{
      await new Promise((resolve,reject)=>{
       const timer=setTimeout(()=>{chrome.tabs.onUpdated.removeListener(listener);reject(Error('Страница Госзакупа не загрузилась. Продолжите очередь после проверки сайта.'));},30000);
       const listener=(id,change)=>{if(id===tab.id&&change.status==='complete'){clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);resolve();}};
       chrome.tabs.onUpdated.addListener(listener);
       chrome.tabs.get(tab.id).then(current=>{if(current.status==='complete')listener(tab.id,{status:'complete'});},reject);
      });
      await runJob();
     }catch(error){const job=await readJob();if(job)await waitRecovery(job,error.message);}
     finally{running=false;}
    })();
   }
  }catch(error){if(message.auto_start===true){running=false;throw error;}}
  try{await chrome.action.setBadgeText({text:'1'});await chrome.action.setBadgeBackgroundColor({color:'#087ee5'});}catch{}
  return {prepared:true,opened};
 }finally{transferring=false;}
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(message?.type==='bridge:account'){
  if(!trustedAccountSender(sender))return false;
  freshAccount(sender).then(()=>respond({bound:true}),error=>respond({error:error.message}));return true;
 }
 if(message?.type==='bridge:queue-check'){
  if(!trustedPreparationSender(sender)||new URL(sender.url).pathname!=='/workspace/drafts/')return false;
  (async()=>{
   await authorizeSender(message,sender);
   if(running||transferring||['running','waiting'].includes((await readJob())?.status))throw Error('Сначала остановите заполнение очереди.');
   transferring=true;
   try{
    const t=message.target;
    if(!t||!/^\d{12}$/.test(t.company_bin)||!/^\d{6,}-\d+$/.test(t.announcement_number)||!/^\d+-[А-Яа-яA-Za-z0-9]+$/.test(t.lot_number)||!/^\d{1,30}$/.test(t.number))throw Error('Некорректные данные проверки.');
    const tab=await chrome.tabs.create({url:'https://zakup.gov.kz/cabinet/purchase/my-applications'});
    await new Promise((resolve,reject)=>{
     const done=error=>{clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);error?reject(error):resolve();};
     const listener=(id,change)=>{if(id===tab.id&&change.status==='complete')done();};
     const timer=setTimeout(()=>done(Error('Кабинет не загрузился.')),30000);chrome.tabs.onUpdated.addListener(listener);
     chrome.tabs.get(tab.id).then(current=>{if(current.status==='complete')done();},done);
    });
    await authorizeSender(message,sender);
    const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:checkSavedApplication,args:[t]});
    await authorizeSender(message,sender);
    if(results.length!==1||!results[0].result)throw Error('Ответ проверки не получен.');
    return {result:results[0].result};
   }finally{transferring=false;}
  })().then(respond,error=>respond({error:error.message}));return true;
 }
 if(message?.type==='bridge:queue-stop'){
  if(!trustedPreparationSender(sender)||new URL(sender.url).pathname!=='/workspace/drafts/')return false;
  authorizeSender(message,sender).then(()=>readJob()).then(job=>{
   if(job?.appOrigin!==new URL(sender.url).origin)throw Error('Очередь другого приложения.');
   if(job.accountScope!==message.account.scope)throw Error('Очередь другого аккаунта.');
   return command({type:'draft:stop'});
  }).then(()=>respond({stopped:true}),error=>respond({error:error.message}));return true;
 }
 if(message?.type==='bridge:queue-status'){
  if(!trustedPreparationSender(sender)||new URL(sender.url).pathname!=='/workspace/drafts/')return false;
  authorizeSender(message,sender).then(()=>command({type:'draft:get'})).then(job=>{
   if(!job?.queue_context||job.appOrigin!==new URL(sender.url).origin){respond({snapshot:null});return;}
   if(job.accountScope!==message.account.scope){respond({snapshot:null});return;}
   respond({queue_context:job.queue_context,snapshot:{status:job.status,number:job.number||'',message:job.message||'',
    intent:job.intent||'',phase:job.phase||'',company_bin:job.packet.company_bin,announcement_number:job.packet.announcement_number,lot_number:job.packet.lot_number,
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

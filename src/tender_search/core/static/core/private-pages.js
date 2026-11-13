// Verify account changes without blanking an already verified, visible page.
try{sessionStorage.removeItem('htmx-history-cache');localStorage.removeItem('htmx-history-cache');}catch(_error){}
(()=>{
 const style=document.createElement('style');style.textContent='html.account-locked body>*{visibility:hidden!important}html.account-locked #account-gate{visibility:visible!important;display:grid;place-content:center;position:fixed;inset:0;background:#f3f6fa;z-index:2147483647;color:#193047;text-align:center;padding:24px}#account-connection{position:fixed;right:16px;bottom:16px;z-index:1000;max-width:360px;padding:10px 14px;border:1px solid #e9ce94;border-radius:8px;background:#fff8e8;color:#795915;font-size:12px}#account-connection[hidden]{display:none}';document.head.append(style);
 document.documentElement.classList.add('account-locked');
 function init(){
  const expected=document.body.dataset.accountScope||'';
  try{localStorage.setItem('app-account-scope',expected);}catch(_error){}
  if(!expected){document.documentElement.classList.remove('account-locked');return;}
  const gate=document.createElement('div');gate.id='account-gate';gate.setAttribute('role','status');gate.setAttribute('aria-live','polite');gate.hidden=true;document.body.append(gate);
  const notice=document.createElement('div');notice.id='account-connection';notice.setAttribute('role','status');notice.hidden=true;document.body.append(notice);
  let checking=false,invalid=false,timer,revision=0,rerun=false,verified=false,needsVerification=true,lastCheck=0,activeRevision=-1;
  function lock(text='Проверяем учётную запись…'){gate.textContent=text;gate.hidden=false;document.documentElement.classList.add('account-locked');}
  function unlock(){gate.hidden=true;document.documentElement.classList.remove('account-locked');}
  function changed(){if(invalid)return;invalid=true;revision++;clearTimeout(timer);lock('Учётная запись изменилась. Обновляем страницу…');document.body.replaceChildren(gate);window.location.reload();}
  async function check(force=false){
   if(invalid||document.hidden)return;
   // Focus, visibility and pageshow often arrive together. Keep one valid request.
   if(checking){if(activeRevision!==revision)rerun=true;return;}
   if(!force&&!needsVerification&&Date.now()-lastCheck<30000)return;
   const version=revision;activeRevision=version;checking=true;lastCheck=Date.now();clearTimeout(timer);
   if(needsVerification)lock();
   try{
    const response=await fetch('/account/session/',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(!response.ok||response.redirected)throw Error();
    const data=await response.json();
    if(invalid||version!==revision||document.hidden)return;
    if(!data.authenticated||data.scope!==expected){changed();return;}
    verified=true;needsVerification=false;notice.hidden=true;unlock();
   }catch(_error){
    if(!invalid&&version===revision&&!document.hidden){
     if(!verified||needsVerification)lock('Не удалось проверить учётную запись. Повторяем подключение…');
     else{notice.textContent='Временный сбой соединения. Повторяем проверку в фоне…';notice.hidden=false;}
     timer=setTimeout(()=>void check(true),10000);
    }
   }finally{
    checking=false;
    if(rerun){rerun=false;if(needsVerification&&!invalid&&!document.hidden)void check(true);}
   }
  }
  function suspend(){revision++;needsVerification=true;clearTimeout(timer);notice.hidden=true;lock();}
  window.addEventListener('focus',()=>void check());
  window.addEventListener('pagehide',suspend);
  window.addEventListener('pageshow',event=>{if(event.persisted){suspend();void check(true);}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else void check(true);});
  // A different account is an immediate privacy boundary, even while offline.
  window.addEventListener('storage',event=>{if(event.key==='app-account-scope'&&event.newValue!==expected)changed();});
  window.addEventListener('app:account-invalid',changed);
  window.addEventListener('online',()=>{if(!notice.hidden||needsVerification)void check(true);});
  void check(true);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();

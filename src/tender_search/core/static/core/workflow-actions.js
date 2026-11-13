// Existing server transitions, progressively enhanced without document navigation.
(()=>{
 let busy=false,uncertain=false,revision=0,reconcileTimer;
 const scope=()=>document.body.dataset.accountScope||'';
 const notice=()=>{let node=document.getElementById('workflow-action-status');if(!node){node=document.createElement('div');node.id='workflow-action-status';node.className='notice';node.setAttribute('role','status');node.setAttribute('aria-live','polite');document.querySelector('main').prepend(node);}return node;};
 function message(text,error=false){const node=notice();node.replaceChildren();node.className=error?'error notice':'notice';node.textContent=text;return node;}
 function sameAccount(expected,actual){if(!expected||scope()!==expected||actual!==expected){window.dispatchEvent(new Event('app:account-invalid'));throw Error('Учётная запись изменилась. Откройте страницу заново.');}}
 function emit(root,name){root.dispatchEvent(new CustomEvent(name,{bubbles:true,detail:{elt:root,target:root}}));}
 async function refresh(expected,version=revision){
  const response=await fetch(location.href,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(20000)});
  if(!response.ok||response.redirected)throw Error('Не удалось обновить состояние.');
  const source=new DOMParser().parseFromString(await response.text(),'text/html');
  sameAccount(expected,source.body.dataset.accountScope);
  if(version!==revision)return false;
  const workspace=document.getElementById('collection-workspace');
  const x=window.scrollX,y=window.scrollY;
  if(workspace){
   const next=source.getElementById('collection-workspace');if(!next)throw Error('Не удалось обновить воронку.');
   // Preserve drafts in fields that remain on this stage after a table refresh.
   const drafts=[...workspace.querySelectorAll('textarea,input:not([type=hidden]):not([type=submit]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=button])')].map(field=>({name:field.name,id:field.id,row:field.closest('tr')?.querySelector('[data-lot-id]')?.dataset.lotId,value:field.value}));
   for(const draft of drafts){const fields=[...next.querySelectorAll('textarea,input')];const field=fields.find(f=>(draft.id?f.id===draft.id:f.name===draft.name&&f.closest('tr')?.querySelector('[data-lot-id]')?.dataset.lotId===draft.row));if(field)field.value=draft.value;}
   workspace.replaceWith(next);window.htmx?.process(next);emit(next,'htmx:afterSwap');
  }else{
   if(!source.getElementById('lot-detail'))throw Error('Не удалось обновить карточку.');
   // Do not touch the specification, document viewers, note or price fields.
   for(const id of ['lot-header','lot-workflow-region','lot-triage-region']){
    const current=document.getElementById(id),next=source.getElementById(id);
    if(current&&next){current.replaceWith(next);window.htmx?.process(next);}
   }
  }
  for(const id of ['sidebar-collection-count','sidebar-favorite-count']){const current=document.getElementById(id),next=source.getElementById(id);if(current&&next)current.textContent=next.textContent;}
  uncertain=false;
  requestAnimationFrame(()=>window.scrollTo(x,y));
  return true;
 }
 function applyConfirmed(result){
  const root=document.getElementById('collection-workspace'),state=result.workspace;
  if(!root||!result.ok||!state||!Array.isArray(state.lots)||!state.lots.length||!state.counts||!Number.isInteger(state.collection_count)||!Number.isInteger(state.favorite_count))return false;
  const parameters=new URL(location.href).searchParams;
  if(state.profile!==(parameters.get('profile')||'all'))return false;
  const stage=location.pathname==='/workspace/drafts/'?'preparing':(parameters.get('stage')||'all'),current=stage==='all'?'new':stage,rows=[];
  for(const lot of state.lots){
   if(lot.stage===current)return false;
   const item=[...root.querySelectorAll('[data-lot-id]')].find(node=>node.dataset.lotId===String(lot.lot_id));
   const row=item?.closest('tr');if(!row)return false;rows.push(row);
  }
  const x=window.scrollX,y=window.scrollY;
  for(const row of new Set(rows))row.remove();
  for(const link of root.querySelectorAll('.collection-funnel .funnel-step')){
   const key=new URL(link.href,location.href).searchParams.get('stage')||'all',number=link.querySelector('strong');
   if(number)number.textContent=String(state.counts[key]||0);
  }
  root.dataset.collectionCount=state.collection_count;root.dataset.favoriteCount=state.favorite_count;
  for(const [id,key] of [['sidebar-collection-count','collection_count'],['sidebar-favorite-count','favorite_count']]){const node=document.getElementById(id);if(node)node.textContent='('+state[key]+')';}
  requestAnimationFrame(()=>window.scrollTo(x,y));
  return true;
 }
 document.addEventListener('htmx:beforeRequest',event=>{if((busy||uncertain)&&event.detail.elt?.id==='collection-workspace')event.preventDefault();});
 document.addEventListener('htmx:beforeSwap',event=>{if((busy||uncertain)&&event.detail.target?.id==='collection-workspace')event.preventDefault();});
 document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('form[data-workflow-action]'))return;
  const root=form.closest('#collection-workspace,#lot-detail');if(!root)return;
  event.preventDefault();if(busy||uncertain)return;
  // A control named "action" shadows HTMLFormElement.action (archive/star forms).
  const expected=scope(),action=new URL(form.getAttribute('action')||location.href,location.href);
  if(action.origin!==location.origin)return;
  const data=new FormData(form);if(event.submitter?.name)data.append(event.submitter.name,event.submitter.value);
  const version=++revision;clearTimeout(reconcileTimer);
  const headers={'X-Workflow-Async':'1','X-Account-Scope':expected};
  if(root.id==='collection-workspace')headers['X-Workspace-Profile']=new URL(location.href).searchParams.get('profile')||'all';
  busy=true;root.dataset.workflowBusy='true';emit(root,'workflow:before');window.htmx?.trigger(root,'htmx:abort');
  const buttons=[...root.querySelectorAll('form[data-workflow-action] button')].map(button=>[button,button.disabled]);buttons.forEach(([button])=>{button.disabled=true;});
  const button=event.submitter,label=button?.textContent;if(button)button.textContent='Перемещаем…';
  message('Обновляем этап лота…');
  let confirmed=false;
  try{
   const response=await fetch(action.href,{method:'POST',body:data,credentials:'same-origin',headers,signal:AbortSignal.timeout(20000)});
   if(!response.ok||response.redirected)throw Error('Не удалось подтвердить перемещение.');
   const result=await response.json();sameAccount(expected,result.scope);confirmed=true;
   if(applyConfirmed(result)){
    // Refill pagination once the user pauses, not before acknowledging the move.
    // A response from an older move must never restore an already moved row.
    const reconcile=()=>{
     if(version!==revision)return;
     if(busy||uncertain||document.activeElement?.matches('input,textarea,select,[contenteditable=true]')){reconcileTimer=setTimeout(reconcile,800);return;}
     void refresh(expected,version).catch(()=>{/* confirmed state remains visible; a later refresh can refill pagination */});
    };
    if(location.pathname!=='/workspace/drafts/')reconcileTimer=setTimeout(reconcile,800);
   }else await refresh(expected,version);
   message(result.messages.map(m=>m.text).join(' ')||(result.ok?'Этап лота обновлён.':'Перемещение недоступно.'),!result.ok);
   document.dispatchEvent(new CustomEvent('workflow:changed'));
  }catch(error){
   uncertain=true;
   const node=message((confirmed?'Ответ сервера получен, но интерфейс не обновился. ':'Результат не подтверждён. ')+ 'Проверьте состояние перед повторным действием.',true);
   const retry=document.createElement('button');retry.type='button';retry.className='outline';retry.textContent='Проверить состояние';node.append(document.createTextNode(' '),retry);
   retry.addEventListener('click',async()=>{retry.disabled=true;try{await refresh(expected);message('Состояние обновлено.');document.dispatchEvent(new CustomEvent('workflow:changed'));}catch(_error){retry.disabled=false;}});
  }finally{
   busy=false;delete root.dataset.workflowBusy;
   buttons.forEach(([old,disabled])=>{if(old.isConnected&&!uncertain)old.disabled=disabled;});if(button?.isConnected)button.textContent=label;
   emit(document.getElementById('lot-detail')||document.getElementById('collection-workspace')||root,'workflow:after');
  }
 });
})();

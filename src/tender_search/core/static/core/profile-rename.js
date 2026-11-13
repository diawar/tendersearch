// Delegated events also work after workspace background replacements.
(()=>{
 const finish=root=>{root.classList.remove('is-editing');root.querySelector('[data-rename-form]').hidden=true;root.querySelector('[data-profile-label]').hidden=false;root.querySelector('[data-rename-start]').hidden=false;root.querySelector('[data-rename-error]').hidden=true;};
 document.addEventListener('click',event=>{
  const start=event.target.closest('[data-rename-start]'),cancel=event.target.closest('[data-rename-cancel]');if(!start&&!cancel)return;
  event.preventDefault();const root=(start||cancel).closest('[data-profile-rename]'),form=root.querySelector('form');
  if(cancel){if(!form.dataset.saving){finish(root);root.querySelector('[data-rename-start]').focus();}return;}
  root.classList.add('is-editing');form.hidden=false;start.hidden=true;root.querySelector('[data-profile-label]').hidden=true;const input=form.elements.namedItem('name');input.value=root.querySelector('[data-profile-label]').textContent;input.focus();input.select();
 });
 document.addEventListener('keydown',event=>{const form=event.target.closest('[data-rename-form]');if(event.key==='Escape'&&form&&!form.dataset.saving){event.preventDefault();finish(form.closest('[data-profile-rename]'));form.closest('[data-profile-rename]').querySelector('[data-rename-start]').focus();}});
 document.addEventListener('htmx:beforeRequest',event=>{if(event.detail.elt?.id==='collection-workspace'&&event.detail.elt.querySelector('[data-profile-rename].is-editing'))event.preventDefault();});
 document.addEventListener('submit',async event=>{
  const form=event.target.closest('[data-rename-form]');if(!form)return;event.preventDefault();if(form.dataset.saving)return;
  const root=form.closest('[data-profile-rename]'),error=root.querySelector('[data-rename-error]'),name=form.elements.namedItem('name').value.trim();error.hidden=true;
  if(!name){error.textContent='Введите название подборки.';error.hidden=false;return;}
  const payload=new URLSearchParams(new FormData(form));payload.set('name',name);form.dataset.saving='true';form.querySelectorAll('button,input').forEach(el=>el.disabled=true);
  try{const response=await fetch(form.action,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':payload.get('csrfmiddlewaretoken')},body:payload,signal:AbortSignal.timeout(15000)});if(response.redirected)throw Error('Войдите в приложение заново.');const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось изменить название.');
   for(const other of document.querySelectorAll('[data-profile-rename]'))if(other.dataset.profileRename===String(data.id)){other.querySelector('[data-profile-label]').textContent=data.name;other.querySelector('[name=name]').value=data.name;other.querySelector('[data-rename-start]').setAttribute('aria-label','Переименовать подборку '+data.name);finish(other);}
   root.querySelector('[data-rename-start]').focus();
  }catch(reason){error.textContent=reason.message;error.hidden=false;}finally{delete form.dataset.saving;form.querySelectorAll('button,input').forEach(el=>el.disabled=false);}
 });
})();

function tagIcon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('icon');svg.setAttribute('aria-hidden','true');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href',document.body.dataset.icons+'#'+name);svg.append(use);return svg;}
document.querySelectorAll('[data-tags]').forEach(editor => {
 const source=editor.querySelector('textarea'), list=editor.querySelector('.tag-list'), entry=editor.querySelector('.tag-entry');
 let tags=source.value.split(/[\s;,]+/).map(s=>s.trim()).filter(Boolean); source.hidden=true; source.required=false;
 editor.addEventListener('remove-term',event=>{tags=tags.filter(term=>term!==event.detail);render();});
 const sync=()=>{source.value=tags.join('; ');source.dispatchEvent(new Event('change',{bubbles:true}));};
 function render(){ list.replaceChildren(); tags.forEach((text,index)=>{const chip=document.createElement('span');chip.className='tag '+(editor.dataset.tags==='exclude'?'tag-exclusion':'tag-keyword');const label=document.createElement('span');label.textContent=text;const edit=document.createElement('button');edit.type='button';edit.className='tag-edit';edit.append(tagIcon('pencil'));edit.setAttribute('aria-label','Изменить '+text);edit.onclick=()=>{const input=document.createElement('input');input.value=text;input.maxLength=150;input.className='tag-inline';chip.replaceChildren(input);let done=false;const finish=()=>{if(done)return;done=true;const value=input.value.trim();const words=value.split(/[\s;,]+/).filter(Boolean);tags.splice(index,1,...words);tags=[...new Set(tags)];sync();render();};input.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();finish();entry.focus();}if(e.key==='Escape'){done=true;render();}};input.onblur=finish;input.focus();input.select();};const remove=document.createElement('button');remove.type='button';remove.append(tagIcon('x'));remove.setAttribute('aria-label','Удалить '+text);remove.onclick=()=>{tags.splice(index,1);sync();render();};chip.append(label,edit,remove);list.append(chip);});sync();}
 function add(){const values=entry.value.split(/[\s;,]+/).map(s=>s.trim()).filter(Boolean);if(values.some(s=>s.length>150)){entry.setCustomValidity('Слово не должно быть длиннее 150 символов.');entry.reportValidity();return false;}tags=[...new Set([...tags,...values])];entry.value='';entry.setCustomValidity('');render();return true;}
 entry.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===';'||e.key===','||e.key===' '){e.preventDefault();add();}});entry.addEventListener('input',()=>entry.setCustomValidity(''));entry.addEventListener('blur',()=>{if(entry.value.trim())add();});editor.closest('form').addEventListener('submit',e=>{if(!add())e.preventDefault();});render();
});
document.querySelectorAll('.procurement-group').forEach(group=>{
 const toggle=group.querySelector('[data-group-toggle]'), boxes=[...group.querySelectorAll('.procurement-children input[type="checkbox"]')];
 const sync=()=>{const count=boxes.filter(box=>box.checked).length;toggle.checked=count===boxes.length&&count>0;toggle.indeterminate=count>0&&count<boxes.length;};
 toggle.addEventListener('click',event=>event.stopPropagation());
 toggle.addEventListener('change',()=>{const checked=toggle.checked;boxes.forEach(box=>{box.checked=checked;box.dispatchEvent(new Event('change',{bubbles:true}));});sync();});
 boxes.forEach(box=>box.addEventListener('change',sync));sync();
});
// Both summaries use the same grouping, while the form still submits source IDs.
function selectedFilterItems(root,field){
 const boxes=[...root.querySelectorAll('input[name="'+field+'"]')], handled=new Set(), items=[];
 for(const box of boxes){
  if(!box.checked||handled.has(box))continue;
  const group=box.closest('.procurement-group');
  const members=group?boxes.filter(candidate=>candidate.closest('.procurement-group')===group):[];
  if(group&&members.length&&members.every(candidate=>candidate.checked)){
   members.forEach(candidate=>handled.add(candidate));
   items.push({label:group.dataset.groupLabel,boxes:members});
  }else{handled.add(box);items.push({label:box.closest('label').textContent.trim(),boxes:[box]});}
 }
 return items;
}
function clearFilterBoxes(boxes){
 boxes.forEach(box=>{box.checked=false;});
 boxes.forEach(box=>box.dispatchEvent(new Event('change',{bubbles:true})));
}
for (const field of ['region','city','status','method']) {
 const boxes=[...document.querySelectorAll('input[name="'+field+'"]')];
 const counter=document.querySelector('[data-count="'+field+'"]');
 const update=()=>{const count=boxes.filter(b=>b.checked).length;if(counter)counter.textContent=count?'('+count+')':'';const preview=document.querySelector('[data-preview="'+field+'"]');if(preview){preview.replaceChildren();selectedFilterItems(document,field).forEach(item=>{const chip=document.createElement('span');chip.className='selected-filter';const label=document.createElement('span');label.textContent=item.label;const remove=document.createElement('button');remove.type='button';remove.className='remove-filter';remove.setAttribute('aria-label','Убрать условие: '+item.label);remove.append(tagIcon('x'));remove.addEventListener('click',()=>clearFilterBoxes(item.boxes));chip.append(label,remove);preview.append(chip);});}};
 boxes.forEach(b=>b.addEventListener('change',update));update();
}

const dropdowns=[...document.querySelectorAll('.check-dropdown')];
document.addEventListener('click',event=>{dropdowns.forEach(dropdown=>{if(!dropdown.contains(event.target))dropdown.open=false;});});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){dropdowns.forEach(dropdown=>dropdown.open=false);}});
document.addEventListener('focusin',event=>{dropdowns.forEach(dropdown=>{if(!dropdown.contains(event.target))dropdown.open=false;});});

const profileForm=document.querySelector('#procurement-search-form');
if(profileForm){
 const save=profileForm.querySelector('button[value="save"]');
 function signature(){
  const values=[...new FormData(profileForm).entries()].filter(([key])=>!['csrfmiddlewaretoken','profile_id','action'].includes(key));
  profileForm.querySelectorAll('.tag-entry,.tag-inline').forEach((input,index)=>{if(input.value.trim())values.push(['pending-'+index,input.value.trim()]);});
  return JSON.stringify(values);
 }
 const savedSignature=profileForm.dataset.saved==='true'?signature():null;
 const updateSave=()=>{if(save)save.disabled=savedSignature!==null&&signature()===savedSignature;};
 profileForm.addEventListener('input',updateSave);profileForm.addEventListener('change',updateSave);profileForm.addEventListener('click',()=>queueMicrotask(updateSave));updateSave();
}

document.addEventListener('click',event=>{
 const button=event.target.closest('[data-condition-field]');if(!button)return;
 const form=document.querySelector('#procurement-search-form');if(!form)return;
 const field=button.dataset.conditionField,value=button.dataset.conditionValue;
 if(['terms','exclude'].includes(field)){
  form.querySelector('[data-tags="'+field+'"]').dispatchEvent(new CustomEvent('remove-term',{detail:value}));
 }else if(['status','method','region','city'].includes(field)){
  const values=button.dataset.conditionValues?JSON.parse(button.dataset.conditionValues):null;
  clearFilterBoxes([...form.querySelectorAll('input[name="'+field+'"]')].filter(box=>values?values.includes(box.value):box.closest('label').textContent.trim()===value));
 }else{
  const input=form.elements.namedItem(field);if(input){if(input.type==='checkbox')input.checked=false;else input.value=['mode','kind','depth'].includes(field)?value:'';input.dispatchEvent(new Event('change',{bubbles:true}));}
 }
 button.closest('.condition-chip').remove();const hint=document.querySelector('.conditions-changed');if(hint)hint.hidden=false;
});

// Interval is adjusted with native spinner arrows only; preserve mouse spinner controls.
document.addEventListener('keydown',event=>{
 if(!event.target.matches('.profile-notifications input[name="hours"]'))return;
 if(!['ArrowUp','ArrowDown','Tab','Escape'].includes(event.key))event.preventDefault();
});
for(const eventName of ['paste','drop','cut']){
 document.addEventListener(eventName,event=>{if(event.target.matches('.profile-notifications input[name="hours"]'))event.preventDefault();});
}

document.addEventListener('click',event=>{
 const button=event.target.closest('[data-hours-step]');if(!button)return;
 const form=button.closest('.profile-notifications'), input=form.querySelector('input[name="hours"]');
 const value=Math.min(12,Math.max(1,Math.round(Number(input.value)||1)+Number(button.dataset.hoursStep)));
 input.value=String(value);form.querySelector('.interval-value').textContent=String(value);
 input.dispatchEvent(new Event('change',{bubbles:true}));
});

const conditionsForm=document.querySelector('#procurement-search-form');
const liveConditions=document.querySelector('#selected-conditions .conditions-live');
if(conditionsForm&&liveConditions){
 function renderConditions(){
  liveConditions.replaceChildren();
  const value=name=>conditionsForm.elements.namedItem(name)?.value||'';
  const add=(label,field,val,tone,removable=true,selectedValues=null)=>{
   const chip=document.createElement('span');chip.className='condition-chip '+tone;
   const sign=document.createElement('b');sign.textContent=field==='exclude'?'−':'+';
   const text=document.createElement('span');text.textContent=label;chip.append(sign,text);
   if(removable){const button=document.createElement('button');button.type='button';button.className='remove-filter';button.dataset.conditionField=field;button.dataset.conditionValue=val;if(selectedValues)button.dataset.conditionValues=JSON.stringify(selectedValues);button.setAttribute('aria-label','Убрать условие: '+label);button.append(tagIcon('x'));chip.append(button);}
   liveConditions.append(chip);
  };
  const words=name=>value(name).split(/[\s;,]+/).filter(Boolean);
  words('terms').forEach(word=>add(word,'terms',word,'condition-keyword'));
  for(const [name,label] of [['amount_min','Сумма от'],['amount_max','Сумма до'],['customer_bin','БИН заказчика']]){if(value(name))add(label+': '+value(name)+(name.startsWith('amount')?' ₸':''),name,value(name),'condition-procurement');}
  for(const [name,label] of [['status','Статус'],['method','Способ закупки'],['region','Область'],['city','Город']]){
   selectedFilterItems(conditionsForm,name).forEach(item=>add(label+': '+item.label,name,item.label,'condition-procurement',true,item.boxes.map(box=>box.value)));
  }
  if(conditionsForm.elements.namedItem('only_advance')?.checked)add('Только с авансом','only_advance','on','condition-procurement');
  for(const [name,label] of [['deadline_from','Окончание приёма от'],['deadline_to','Окончание приёма до']]){if(value(name))add(label+': '+value(name).split('-').reverse().join('.'),name,value(name),'condition-procurement');}
  if(value('number'))add((value('number_kind')==='announcement_number'?'Номер объявления: ':'Номер лота: ')+value('number'),'number',value('number'),'condition-procurement');
  words('exclude').forEach(word=>add(word,'exclude',word,'condition-exclude'));
 }
 conditionsForm.addEventListener('input',renderConditions);conditionsForm.addEventListener('change',renderConditions);renderConditions();
}

function setSearchBusy(busy){
 document.querySelectorAll('button[type="submit"][name="action"][value="search"], button.conditions-search').forEach(button=>{
  const cancelUrl=document.querySelector('#search-results')?.dataset.cancelUrl;
  if(button.classList.contains('conditions-search')){
   if(!button.dataset.searchMarkup)button.dataset.searchMarkup=button.innerHTML;
   const cancelling=busy&&Boolean(cancelUrl);
   button.value=cancelling?'cancel':'search';button.classList.toggle('cancel-search',cancelling);
   button.title=cancelling?'Отменить поиск':'Запустить поиск';button.setAttribute('aria-label',button.title);
   if(cancelling){button.setAttribute('formaction',cancelUrl);button.setAttribute('formnovalidate','');button.innerHTML='<svg class="icon" aria-hidden="true"><use href="'+document.body.dataset.icons+'#x"></use></svg>';button.disabled=false;return;}
   button.removeAttribute('formaction');button.removeAttribute('formnovalidate');button.innerHTML=button.dataset.searchMarkup;
  }
  button.disabled=busy;button.classList.toggle('is-searching',busy);button.setAttribute('aria-busy',String(busy));
  const ring=button.querySelector('.button-spinner');
  if(busy&&!ring){const spinner=document.createElement('span');spinner.className='search-spinner button-spinner';spinner.setAttribute('aria-hidden','true');button.prepend(spinner);}
  if(!busy&&ring)ring.remove();
 });
}
function syncSearchBusy(){setSearchBusy(document.querySelector('#search-results')?.dataset.searchPending==='true');}
if(conditionsForm){conditionsForm.addEventListener('submit',event=>{if(!event.defaultPrevented&&event.submitter?.value==='search')setSearchBusy(true);});}
document.addEventListener('htmx:afterSwap',syncSearchBusy);
window.addEventListener('pageshow',syncSearchBusy);
syncSearchBusy();

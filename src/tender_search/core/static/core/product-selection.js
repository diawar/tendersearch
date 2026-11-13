// Catalogue text is untrusted. Render it with textContent; never submit the form.
(()=>{
 const root=document.getElementById('product-selection');if(!root)return;
 const button=root.querySelector('#analyze-product'),status=root.querySelector('#product-selection-status'),results=root.querySelector('#product-selection-results'),form=document.getElementById('preparation-form');
 let busy=false,original='',configured=root.dataset.aiConfigured==='true';
 const fingerprint=()=>JSON.stringify(['product_name','manufacturer','country','unit_gross'].map(name=>form.elements.namedItem(name)?.value));
 const money=value=>Number(value).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' ₸';
 const node=(tag,text,parent)=>{const el=document.createElement(tag);el.textContent=text;parent.append(el);return el;};
 const reviewableReasons=new Set(['ИИ не подтвердил полноту разбора требований.','Не все требования к товару подтверждены.']);
 function manualRequirements(product){
  const reasons=product.blocked_reasons||[],checks=product.checks||[];
  if(product.eligible||!product.price||!reasons.length||reasons.some(r=>!reviewableReasons.has(r))||checks.some(c=>!['delivery','supplier'].includes(c.scope)&&c.state==='fail'))return [];
  if(checks.some(c=>['quantity','unit'].includes(c.scope)&&c.state!=='pass'))return [];
  const requirements=checks.filter(c=>c.scope==='product'&&c.state==='unknown').map(c=>c.requirement);
  if(reasons.includes('ИИ не подтвердил полноту разбора требований.')||!checks.some(c=>c.scope==='product'))requirements.push('Полностью сверил техническую спецификацию: все требования к товару учтены и выполнены.');
  return [...new Set(requirements)];
 }
 function apply(product,confirmed=[]){
  const required=manualRequirements(product),manual=!product.eligible&&required.length&&required.every(r=>confirmed.includes(r));
  if((!product.eligible&&!manual)||!product.price)return;
  const name=form.elements.namedItem('product_name'),price=form.elements.namedItem('unit_gross');
  if(!name||!price||name.disabled||price.disabled)return;
  const fields=product.preparation_fields||{product_name:product.name,manufacturer:'',country:''},missing=[];
  for(const [fieldName,label] of [['product_name','Наименование товара'],['manufacturer','Завод-изготовитель'],['country','Страна происхождения']]){
   const field=form.elements.namedItem(fieldName);if(!field||field.disabled)continue;
   const value=fields[fieldName]||'';field.value=value;
   if(!field.value)missing.push(label);field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));
  }
  price.value=product.price.unit_gross;price.dispatchEvent(new Event('input',{bubbles:true}));
  document.dispatchEvent(new CustomEvent('over-product-selected',{detail:{url:product.url}}));
  status.textContent='Подставлено: '+product.name+' · '+(product.price.kind==='online'?'онлайн-цена':'клубная цена')+'. '+(manual?'Неподтверждённые требования проверены вами вручную. Это не подтверждение ИИ. ':'')+(product.unverified.length?'Остались требования для сверки ('+product.unverified.length+'). ':'')+(missing.length?'Каталог не подтвердил: '+missing.join(', ')+'. Заполните вручную. ':'')+'Проверьте требования и сохраните подготовку.';
 }
 function show(data,auto){
  results.replaceChildren();
  node('p',data.engine==='openai'?'Анализ ИИ · '+data.model:'Предыдущий подбор по правилам',results);
  node('p',data.limitation,results);
  if(typeof data.compared==='number')node('p','Загружено карточек: '+data.scanned+' · Сравнено по требованиям: '+data.compared,results);
  if(data.reused_comparisons)node('p','Повторно использовано проверок неизменившихся характеристик: '+data.reused_comparisons+'. Цены и наличие обновлены.',results);
  if(data.summary)node('p',data.summary,results);
  if(data.catalogue_category_selection){const selection=data.catalogue_category_selection;node('p',selection.paths.length?'Разделы Over-shop: '+selection.labels.join('; ')+'. Общий поиск сохранён как резерв.':'Раздел Over-shop не определён однозначно — используется общий поиск каталога.',results);}
  if(data.requirements_plan){
   const obligations=data.requirements_plan.requirements.filter(r=>['delivery','supplier'].includes(r.scope));
   if(obligations.length){const block=node('details','',results);node('summary','Дополнительные обязательства и требования к поставщику ('+obligations.length+')',block);for(const req of obligations)node('p',req.label,block);node('p','Проверяются отдельно: наличие подходящего товара не подтверждает выполнение этих условий.',block);}
   const requirements=node('details','',results);node('summary','Требования из технической спецификации',requirements);
   for(const req of data.requirements_plan.requirements){node('p',req.label+(req.ambiguous?' · Неоднозначное условие':''),requirements);node('blockquote',req.quote+' ['+req.document_id+']',requirements);}
  }
  if(data.required_model)node('p','В техспецификации указана модель: '+data.required_model+'. Автоподстановка возможна только при совпадении модели.',results);
  for(const [index,product] of data.candidates.entries()){
   const card=node('article','',results);card.className='product-candidate';
   const header=node('div','',card);header.className='candidate-header';
   const title=node('div','',header);
   const link=node('a',product.name,title);link.href=product.url;link.target='_blank';link.rel='noopener noreferrer';link.className='candidate-title';
   if(product.category_label)node('p',product.category_label,title).className='candidate-category';
   const technical=product.checks.filter(c=>c.scope==='product');
   const failed=technical.filter(c=>c.state==='fail').length;
   const verdict=node('span',product.eligible?'Можно подставить':failed?'Есть несоответствия':'Требует проверки',header);
   verdict.className='candidate-badge '+(product.eligible?'is-pass':failed?'is-fail':'is-unknown');
   const prices=node('dl','',card);prices.className='candidate-prices';
   for(const [label,value] of [['Онлайн',product.prices.online?money(product.prices.online):'Не указана'],['Клубная',product.prices.club?money(product.prices.club):'Не указана'],['Для заявки, за единицу',product.price?money(product.price.unit_gross)+' · '+(product.price.kind==='online'?'онлайн':'клубная'):'Нет подходящей цены'],['Наличие',product.stock_text||'Не подтверждено']]){
    const item=node('div','',prices);node('dt',label,item);node('dd',value,item);
   }
   if(product.preparation_fields){const fields=node('p','Изготовитель: '+(product.preparation_fields.manufacturer||'не указан в каталоге')+' · Страна происхождения: '+(product.preparation_fields.country||'не указана в каталоге'),card);fields.className='candidate-category';}
   if((product.blocked_reasons||[]).length){
    const blocked=node('div','',card);blocked.className='candidate-blocked';node('strong','Что мешает подстановке',blocked);
    const list=node('ul','',blocked);for(const reason of product.blocked_reasons)node('li',reason,list);
   }
   const counts=node('div','',card);counts.className='candidate-counts';
   for(const [state,label] of [['pass','Подтверждено'],['fail','Не совпадает'],['unknown','Не подтверждено']])node('span',label+': '+technical.filter(c=>c.state===state).length,counts).className='candidate-badge is-'+state;
   const checks=product.checks.filter(c=>!['delivery','supplier'].includes(c.scope));
   if(checks.length){
    const comparison=node('details','',card);comparison.className='candidate-comparison';comparison.open=index===0;
    node('summary','Сравнение требований ('+checks.length+')',comparison);
    const tools=node('div','',comparison);tools.className='candidate-filters';tools.setAttribute('role','group');tools.setAttribute('aria-label','Показать требования');
    const attention=node('button','Требуют внимания ('+checks.filter(c=>c.state!=='pass').length+')',tools),all=node('button','Все ('+checks.length+')',tools);
    for(const control of [attention,all]){control.type='button';control.className='outline';}
    const scroll=node('div','',comparison);scroll.className='candidate-comparison-scroll';scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','Сравнение характеристик '+product.name);
    const table=node('table','',scroll);table.className='candidate-checks';const row=node('tr','',node('thead','',table));
    for(const label of ['Требование заказчика','Подтверждение','Результат']){const th=node('th',label,row);th.scope='col';}
    const body=node('tbody','',table),rows=[];
    const order={fail:0,unknown:1,pass:2};
    for(const check of [...checks].sort((a,b)=>(order[a.state]??1)-(order[b.state]??1))){
     const row=node('tr','',body);row.dataset.state=check.state;rows.push(row);
     const requirement=node('td',check.requirement,row);requirement.dataset.label='Требование';
     const actual=node('td','',row);actual.dataset.label='Подтверждение';node('div',check.actual||'Нет сведений',actual);
     if(check.reason)node('p',check.reason,actual).className='candidate-check-reason';
     const result=node('td','',row);result.dataset.label='Результат';
     node('span',check.state==='pass'?'✓ Подтверждено':check.state==='fail'?'✕ Не совпадает':'Не подтверждено',result).className='candidate-badge is-'+(order[check.state]===undefined?'unknown':check.state);
    }
    const empty=node('p','Все проверенные требования подтверждены.',comparison);empty.className='candidate-all-confirmed';
    function filter(onlyAttention){
     rows.forEach(row=>row.hidden=onlyAttention&&row.dataset.state==='pass');
     attention.setAttribute('aria-pressed',String(onlyAttention));all.setAttribute('aria-pressed',String(!onlyAttention));
     empty.hidden=!onlyAttention||checks.some(c=>c.state!=='pass');scroll.hidden=onlyAttention&&!checks.some(c=>c.state!=='pass');
    }
    attention.addEventListener('click',()=>filter(true));all.addEventListener('click',()=>filter(false));filter(checks.some(c=>c.state!=='pass'));
   }
   if(product.unverified.length){const review=node('details','',card);node('summary','Исходные фрагменты для ручной сверки ('+product.unverified.length+')',review);const list=node('ul','',review);for(const text of product.unverified)node('li',text,list);}
   const attributes=node('details','',card);node('summary','Все характеристики из каталога',attributes);
   const attributesList=node('dl','',attributes);attributesList.className='candidate-attributes';
   for(const [key,value] of Object.entries(product.attributes)){node('dt',key,attributesList);node('dd',value,attributesList);}
   const actions=node('div','',card);actions.className='candidate-actions';
   const choose=node('button','Подставить товар и цену',actions);choose.type='button';choose.className='primary';choose.disabled=!product.eligible;choose.addEventListener('click',()=>apply(product));
   if(!product.eligible){
    const required=manualRequirements(product);
    if(required.length){
     const review=node('details','',card);review.className='candidate-manual-review';node('summary','Проверил вручную — подставить',review);
     node('p','Подтвердите каждый пункт после сверки с техспецификацией и характеристиками товара. Подстановка заполнит поля подготовки; заявку на портал не отправляет.',review);
     const confirmations=[];
     for(const requirement of required){const label=node('label','',review);label.style.display='block';const check=document.createElement('input');check.type='checkbox';label.append(check,document.createTextNode(' '+requirement));confirmations.push(check);}
     const confirm=node('button','Проверил вручную — подставить',review);confirm.type='button';confirm.className='primary';confirm.disabled=true;
     const ready=()=>confirmations.every(check=>check.checked);
     confirmations.forEach(check=>check.addEventListener('change',()=>{confirm.disabled=!ready();}));
     confirm.addEventListener('click',()=>{if(ready())apply(product,required);});
    }else node('span','Подстановка недоступна: устраните причины выше. Ручное подтверждение не снимает ограничения по цене, наличию, количеству и явные несоответствия.',actions);
   }
  }
  const best=data.candidates.find(p=>p.eligible);
  const saved=data.candidates.filter(p=>p.name===form.elements.namedItem('product_name')?.value);
  if(saved.length===1)document.dispatchEvent(new CustomEvent('over-product-selected',{detail:{url:saved[0].url,preserve:true}}));
  status.textContent=best?'Кандидаты найдены. Непроверенные требования необходимо сверить вручную.':'Подходящий по проверенным ограничениям, наличию и бюджету товар в этой выборке не найден.';
  if(auto&&best&&fingerprint()===original)apply(best);
 }
 async function request(method){
  const response=await fetch(root.dataset.analysisUrl,{method,credentials:'same-origin',headers:method==='POST'?{'X-CSRFToken':form.elements.namedItem('csrfmiddlewaretoken').value}:{},signal:AbortSignal.timeout(20000)});
  if(response.redirected)throw Error('Войдите в приложение заново.');
  const data=await response.json();if(!response.ok)throw Error(data.error||'Подбор недоступен.');return data;
 }
 async function follow(data,auto){
  if(typeof data.ai_configured==='boolean')configured=data.ai_configured;
  for(let n=0;['queued','running'].includes(data.state)&&n<160;n++){
   const stages={requirements:'Выделяем требования из техспецификации…',category:'Определяем подходящие разделы Over-shop…',catalogue:'Загружаем товары и характеристики Over-shop…',comparison:'Сравниваем характеристики товаров с требованиями…'};
   status.textContent=data.state==='queued'?'Подбор ожидает запуска…':stages[data.result?._progress]||'Запускаем анализ требований…';
   if(data.result?._progress==='comparison'&&data.result._comparison_total)status.textContent+=' Проверено '+data.result._comparison_done+' из '+data.result._comparison_total+'.';
   await new Promise(resolve=>setTimeout(resolve,2000));data=await request('GET');
  }
  if(data.state==='done')show(data.result,auto);else if(data.state!=='none')throw Error(data.error||'Подбор ещё выполняется. Откройте страницу позже.');
 }
 button.addEventListener('click',async()=>{if(busy||!configured)return;busy=true;button.disabled=true;original=fingerprint();try{await follow(await request('POST'),true);}catch(e){status.textContent=e.message;}finally{busy=false;button.disabled=!configured;}});
 busy=true;button.disabled=true;
 request('GET').then(data=>follow(data,false)).catch(e=>{status.textContent=e.message;}).finally(()=>{busy=false;button.disabled=!configured;});
})();

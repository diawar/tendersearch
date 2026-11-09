/* Explicit, DOM-only draft preparation. No private API, signing or payments. */
function validateDraftPacket(packet){
 if(!packet||packet.schema!=='goszakup-preparation-v1'||!/^\d{12}$/.test(packet.company_bin)||!/^\d+$/.test(packet.announcement_external_id)||!/^\d+-\d+$/.test(packet.announcement_number)||!/^\d+-[\p{L}\d]+$/u.test(packet.lot_number))throw Error('Загрузите корректную подготовку из приложения.');
 if(packet.announcement_number.split('-')[0]!==packet.announcement_external_id)throw Error('Номера объявления не согласованы.');
 if(String(packet.purchase_method_id)!=='12')throw Error('Автоматическая подготовка пока поддерживает только ЗЦП. Скачайте свежий JSON из приложения.');
 const count=packet.position_count||(packet.single_position===true?1:0);
 if(!Number.isInteger(count)||count<1||count>20)throw Error('Число позиций не подтверждено. Обновите лот и скачайте свежий JSON.');
 if(count>1&&packet.repeat_product!==true)throw Error('В лоте несколько позиций. Подтвердите одинаковый товар для всех позиций либо заполните описание вручную.');
 if(!packet.data||typeof packet.data!=='object')throw Error('Нет данных товара и организации.');
 for(const key of ['product_name','manufacturer','country','legal_address','bank_account','contact_phone'])if(typeof packet.data[key]!=='string'||!packet.data[key].trim()||packet.data[key].length>1000)throw Error('Заполните подготовку товара и реквизиты в приложении.');
 if(packet.data.taxpayer_type!==undefined&&(typeof packet.data.taxpayer_type!=='string'||packet.data.taxpayer_type.length>200))throw Error('Некорректный тип налогоплательщика.');
 if(!/^KZ[0-9A-Z]{18}$/.test(packet.data.bank_account))throw Error('Проверьте ИИК в подготовке.');
 return packet;
}

async function runDraftStep(packet,action='probe',context={}){
 // This function is injected as a self-contained unit into the granted tab.
 let mutationStarted=false;
 const fail=message=>{throw Error(message);};
 const visible=el=>Boolean(el&&el.getClientRects().length)&&getComputedStyle(el).visibility!=='hidden';
 const norm=value=>String(value||'').toLowerCase().replace(/ё/g,'е').replace(/[\u200b-\u200d\ufeff]/g,'').replace(/[*:]/g,'').replace(/\s+/g,' ').trim();
 const text=el=>el?.innerText||el?.textContent||'';
 const compact=value=>norm(value).replace(/[^\p{L}\d]/gu,'');
 const phone=value=>String(value||'').replace(/\D/g,'').replace(/^8(?=\d{10}$)/,'7');
 const wait=async(test,message)=>{
  const until=Date.now()+10000;
  while(Date.now()<until){const value=test();if(value)return value;await new Promise(resolve=>setTimeout(resolve,150));}
  fail(message);
 };
 const click=el=>{if(!visible(el)||el.disabled||el.getAttribute('aria-disabled')==='true')fail('Нужный элемент недоступен.');el.click();};
 const actions=(root=document)=>{
  const found=[...root.querySelectorAll('button,a,[role=button]')].filter(el=>visible(el)&&!el.disabled&&!el.closest('[aria-disabled=true],button:disabled'));
  // A router link can wrap a button. That is one action, not two candidates.
  return found.filter(el=>!found.some(child=>child!==el&&el.contains(child)&&norm(text(child))===norm(text(el))));
 };
 const button=(label,root=document)=>{
  const found=actions(root).filter(el=>norm(text(el))===norm(label));
  if(found.length!==1)fail('Неоднозначная или отсутствующая кнопка «'+label+'».');return found[0];
 };
 const setInput=(el,value)=>{
  if(!el||el.disabled||el.readOnly)fail('Поле недоступно.');
  const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);
  el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
 };
 const fields=label=>[...document.querySelectorAll('.ant-form-item')].filter(el=>visible(el)&&norm(el.querySelector('label')?.textContent)===norm(label));
 const field=label=>{
  const roots=fields(label);
  if(roots.length!==1)fail('Неоднозначное или отсутствующее поле «'+label+'».');return roots[0];
 };
 const selected=root=>{
  const value=root.querySelector('.ant-select-selection-item,.ant-select-content-value');
  if(value)return text(value);
  // Default single-value Select renders the label as a direct text child.
  // Only this explicit selected-state marker confirms it: a search input or
  // placeholder alone must never count as a selection.
  const content=root.querySelector('.ant-select-content-has-value');
  return content?text(content):'';
 };
 const requireAccountFields=()=>{
  for(const root of document.querySelectorAll('.ant-form-item')){
   if(!visible(root)||!root.querySelector('.ant-form-item-required'))continue;
   const combo=root.querySelector('input[role=combobox]'),plain=root.querySelector('input:not([role]):not([type=hidden]):not([type=checkbox]):not([type=radio])');
   if((combo&&!selected(root))||(plain&&!plain.value.trim()))fail('Выберите или заполните «'+text(root.querySelector('label')).trim()+'» вручную, затем продолжите в расширении.');
  }
 };
 const accountMatches=(value,kind)=>kind==='bank'?compact(value).toUpperCase().includes(packet.data.bank_account):compact(value)===compact(packet.data.legal_address);
 const choose=async(root,expected,predicate,{search=true}={})=>{
  if(predicate(selected(root)))return;
  if(selected(root))fail('В списке уже выбрано другое значение. Проверьте его вручную.');
  const input=root.querySelector('input[role=combobox]');
  const selector=root.querySelector('.ant-select-selector,.ant-select-content')||root.querySelector('.ant-select');
  // New portal Select uses content/input rather than the older selector wrapper.
  // Its search input can be zero-width until focus, while the control is usable.
  if(!input||!visible(selector)||input.disabled||selector.closest('.ant-select-disabled,[aria-disabled=true]'))fail('Выпадающий список «'+text(root.querySelector('label')).trim()+'» недоступен.');
  if(!input.readOnly&&input.value)setInput(input,'');
  if(input.getAttribute('aria-expanded')!=='true'){
   selector.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
   selector.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));selector.click();
  }
  input.focus();
  if(search&&!input.readOnly)setInput(input,expected.replace(/\s+/g,' ').trim());
  const options=()=>{
   const owned=input.getAttribute('aria-controls')||input.getAttribute('aria-owns');
   const list=owned?document.getElementById(owned):null;
   let dropdown=list?.closest('.ant-select-dropdown');
   if(!dropdown){const opened=[...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')].filter(visible);if(opened.length!==1)return [];dropdown=opened[0];}
   if(!visible(dropdown))return [];
   return [...dropdown.querySelectorAll('.ant-select-item-option')].filter(el=>visible(el)&&el.getAttribute('aria-disabled')!=='true'&&!el.classList.contains('ant-select-item-option-disabled'));
  };
  await wait(()=>options().length,'Список не загрузился. Выберите значение вручную.');
  const matches=options().filter(el=>predicate(el.getAttribute('title')||text(el)));
  if(matches.length!==1)fail('В списке нет единственного совпадения с подготовкой. Проверьте реквизиты в настройках: они должны совпадать с Госзакупом. Затем скачайте свежий JSON.');
  click(matches[0]);await wait(()=>predicate(selected(root)),'Выбор в поле «'+text(root.querySelector('label')).trim()+'» не подтвердился.');
 };
 const lotTokens=value=>[...new Set((String(value||'').replace(/[–—]/g,'-').match(/\d+-[\p{L}]+\d+/gu)||[]))];
 const selectedRows=()=>[...document.querySelectorAll('tbody tr')].filter(el=>visible(el)&&lotTokens(text(el)).length);
 const exactLotRows=()=>selectedRows().filter(el=>lotTokens(text(el)).includes(packet.lot_number));
 const tab=label=>[...document.querySelectorAll('[role=tab]')].filter(el=>visible(el)&&norm(text(el)).startsWith(norm(label)));
 const openTab=async label=>{
  await wait(()=>tab(label).length,'Вкладка «'+label+'» ещё не загрузилась. Повторите продолжение после загрузки страницы.');
  const matches=tab(label);if(matches.length!==1)fail('Не найдена единственная вкладка «'+label+'».');
  click(matches[0]);await wait(()=>matches[0].getAttribute('aria-selected')==='true','Вкладка не переключилась.');
  await new Promise(resolve=>setTimeout(resolve,250));
 };
 const restrictions=()=>[...document.querySelectorAll('.ant-alert-error,.ant-notification-notice-error')].filter(visible)
  .map(text).find(value=>/необходимо состоять|недостаточно прав|подача заявки недоступна|не удалось|ошибка/i.test(value));
 try{
  const count=packet?.position_count||(packet?.single_position===true?1:0);
  if(!packet||packet.schema!=='goszakup-preparation-v1'||!/^\d{12}$/.test(packet.company_bin)||!/^\d+$/.test(packet.announcement_external_id)||String(packet.purchase_method_id)!=='12'||!Number.isInteger(count)||count<1||count>20||(count>1&&packet.repeat_product!==true)||!packet.data)fail('Подготовка не подтверждена.');
  const url=new URL(location.href),ann=String(packet.announcement_external_id);
  if(url.origin!=='https://zakup.gov.kz'||!url.pathname.startsWith('/cabinet/'))fail('Откройте авторизованный кабинет zakup.gov.kz.');
  const organizationBins=()=>[...new Set([...document.querySelectorAll('header,[role=banner],.ant-layout-header')].filter(visible)
   .flatMap(el=>[...text(el).matchAll(/БИН\s*:?\s*(\d{12})/g)].map(m=>m[1])))];
  await wait(()=>organizationBins().length,'Не удалось прочитать БИН организации в шапке кабинета. Черновик сохранён; обработка остановлена.');
  const bins=organizationBins();
  if(bins.length!==1)fail('БИН в шапке кабинета неоднозначен. Проверьте выбранную организацию; существующий черновик не изменён.');
  if(bins[0]!==packet.company_bin)fail('БИН организации в шапке кабинета не совпадает с подготовкой.');
  const apply='/cabinet/purchase/announcements/'+ann+'/apply';
  const isAnnouncement=url.pathname==='/cabinet/announcement/'+ann;
  if(!isAnnouncement&&url.pathname!==apply&&!url.pathname.startsWith(apply+'/'))fail('Откройте объявление '+packet.announcement_number+' из подготовки.');
  const number=url.searchParams.get('id');
  if(number&&!/^\d+$/.test(number))fail('Некорректный номер черновика.');
  if(number&&context.applicationNumber&&number!==context.applicationNumber)fail('Открыт другой черновик.');
  const result=(state,next,message,extra={})=>({state,next,message,number:number||'',...extra});
  const pageText=document.body.innerText;
  if(/Цену вводите в окне TumarCSP|Подписать цены|Подписать и отправить/.test(pageText))fail('Автоматизация остановлена перед ценой, подписанием и отправкой.');
  if(isAnnouncement){
   if(!pageText.includes(packet.announcement_number))fail('Номер объявления не подтверждён на странице.');
   const candidates=()=>({resumes:actions().filter(el=>/^продолжить заявку\s*№?\s*\d+$/.test(norm(text(el)))),
    creates:actions().filter(el=>norm(text(el))==='подать заявку')});
   await wait(()=>{const c=candidates();return c.resumes.length||c.creates.length;},'Кнопка «Подать заявку» / «Продолжить заявку» не найдена после загрузки. Откройте её вручную и продолжите на шаге реквизитов.');
   const {resumes,creates}=candidates();
   if(context.applicationNumber&&!resumes.length)fail('Ранее подтверждён черновик № '+context.applicationNumber+'. Откройте его в «Моих заявках»; повторное создание отключено.');
   if(resumes.length>1||(!resumes.length&&creates.length!==1))fail('Найдено несколько кнопок создания или продолжения. Откройте нужную форму вручную и продолжите на шаге реквизитов.');
   if(action==='probe')return result('announcement','open_form','Готов переход к форме.',{resuming:resumes.length===1});
   if(action!=='open_form')fail('Действие не соответствует странице.');
   const target=resumes[0]||creates[0];
   const anchor=target.closest('a[href]');
   if(anchor){
    const destination=new URL(anchor.href,url),resumeNumber=resumes.length?text(resumes[0]).match(/\d+$/)?.[0]:'';
    const id=destination.searchParams.get('id'),requestedStep=Number(destination.searchParams.get('step')||'1');
    if(destination.origin!==url.origin||destination.pathname!==apply||destination.username||destination.password||destination.hash||
       [...destination.searchParams.keys()].some(key=>!['id','step'].includes(key))||destination.searchParams.getAll('id').length>1||destination.searchParams.getAll('step').length>1)fail('Ссылка формы не подтверждена.');
    if(!Number.isInteger(requestedStep)||requestedStep<1||requestedStep>3)fail('Черновик уже находится на этапе цены или просмотра. Эти этапы выполняются вручную.');
    if(resumeNumber? id!==resumeNumber||context.applicationNumber&&id!==context.applicationNumber : id!==null)fail('Номер заявки в ссылке продолжения не подтверждён.');
    // Portal links explicitly use target=_blank. The background navigates the
    // already granted tab instead of losing it to an untracked new tab.
    return result('navigate',null,'Открываем '+(resumeNumber?'существующий черновик № '+resumeNumber:'форму заявки')+'.',
     {navigate:destination.href,number:resumeNumber||'',resuming:Boolean(resumeNumber)});
   }
   click(target);
   await wait(()=>location.pathname.startsWith(apply),'Переход не подтверждён. Обновите страницу и проверьте существующую заявку.');
   return result('opened',null,'Форма открыта.');
  }
  const step=Number(url.searchParams.get('step')||'1');
  if(!Number.isInteger(step)||step<1||step>3)fail('Расширение работает только с реквизитами, лотом и описанием товара.');
  const restriction=restrictions();if(restriction)fail(restriction.slice(0,350));
  const fillPath=new RegExp('^'+apply+'/product-description/\\d+/?$');
  if(fillPath.test(url.pathname)){
   if(!number)fail('Номер черновика не подтверждён.');
   await wait(()=>lotTokens(document.body.innerText).includes(packet.lot_number),'Данные нужного лота в описании ещё не загрузились. Продолжите после загрузки страницы.');
   await wait(()=>fields('Наименование товара').length,'Форма описания ещё не загрузилась. Продолжите после загрузки страницы.');
   const names=fields('Наименование товара'),manufacturers=fields('Завод-изготовитель'),countries=fields('Страна происхождения');
   const actualCount=names.length;
   if(!actualCount||manufacturers.length!==actualCount||countries.length!==actualCount)fail('Неполные наборы полей описания: товар '+names.length+', изготовитель '+manufacturers.length+', страна '+countries.length+'. Заполнение остановлено.');
   const summaries=[...document.querySelectorAll('dl')].filter(visible);
   const formLots=lotTokens(summaries.length?summaries.map(text).join(' '):document.body.innerText);
   const exactFormLot=formLots.length===1&&formLots[0]===packet.lot_number;
   if(summaries.length&&!exactFormLot)fail('Номер лота в сведениях формы не совпадает с подготовкой.');
   // Registry plan entries and editable application points are different objects.
   // A single complete group for this exact lot is safe under the user's existing
   // consent to use the same product; extra or incomplete groups still stop.
   const singlePortalGroup=actualCount===1&&count>1&&packet.repeat_product===true&&exactFormLot;
   if(actualCount!==count&&!singlePortalGroup)fail('В подготовке '+count+' записей плана, в форме '+actualCount+' позиций. Проверьте состав вручную.');
   const positionNote=singlePortalGroup?' В форме Госзакупа одна позиция; в реестре '+count+' записей плана.':'';
   const entries=names.map((root,i)=>({name:root.querySelector('input'),manufacturer:manufacturers[i].querySelector('input'),country:countries[i]}));
   const ready=entries.every(({name,manufacturer,country})=>name?.value.trim()===packet.data.product_name.trim()&&manufacturer?.value.trim()===packet.data.manufacturer.trim()&&norm(selected(country))===norm(packet.data.country));
   if(action==='probe')return result('description',ready?'save_description':'fill_description','Описание товара.'+positionNote,{lotConfirmed:true});
   if(action==='fill_description'){
    for(const {name,manufacturer,country} of entries){
     for(const [el,value] of [[name,packet.data.product_name],[manufacturer,packet.data.manufacturer]]){
     if(el.value.trim()&&el.value.trim()!==value.trim())fail('В описании уже есть другие данные. Проверьте их вручную.');setInput(el,value);
     }
     await choose(country,packet.data.country,value=>norm(value)===norm(packet.data.country));
    }
    return result('description_ready',null,'Поля описания заполнены.'+positionNote,{lotConfirmed:true});
   }
   if(action!=='save_description'||!ready)fail('Описание не готово к сохранению.');
   const successSelector='.ant-notification-notice-success,.ant-message-success,[class*="notification-notice-success"],[class*="message-success"]';
   const before=new Set(document.querySelectorAll(successSelector));
   mutationStarted=true;click(button('Сохранить'));
   await wait(()=>[...document.querySelectorAll(successSelector)].find(el=>visible(el)&&!before.has(el)&&/сохран|сәтті/i.test(text(el))),'Сохранение описания не подтверждено. Проверьте его на портале.');
   return result('complete',null,'Черновик готов: нужный лот добавлен, описание товара сохранено.'+positionNote+' Обеспечение, цена, подпись и отправка остаются ручными.',{lotConfirmed:true});
  }
  if(url.pathname===apply+'/product-description'){
   if(!number)fail('Нет номера черновика.');
   await wait(()=>exactLotRows().length,'Список описаний ещё не загрузился или нужный лот отсутствует. Проверьте страницу перед продолжением.');
   const rows=exactLotRows();if(rows.length!==1)fail('Нужный лот не найден однозначно в описаниях.');
   const row=rows[0];
   if(row.querySelector('.ant-tag-success'))return result('complete',null,'Описание нужного лота уже заполнено на портале. Существующие данные не изменены.',{lotConfirmed:true});
   const buttons=[...row.querySelectorAll('button')].filter(el=>visible(el)&&['добавить','заполнить','редактировать'].includes(norm(text(el))));
   if(buttons.length!==1)fail('Не найдено однозначное действие заполнения описания.');
   if(action==='probe')return result('description_list','edit_description','Готов переход к описанию.',{lotConfirmed:true});
   if(action!=='edit_description')fail('Действие не соответствует странице.');
   click(buttons[0]);await wait(()=>fillPath.test(location.pathname),'Переход к описанию не подтверждён.');
   return result('description_opened',null,'Открыто описание нужного лота.',{lotConfirmed:true});
  }
  if(url.pathname!==apply)fail('Этот раздел документов заполняется вручную.');
  if(step===1){
   await wait(()=>fields('Юридический адрес').length&&fields('Банковский счёт').length&&fields('Контактный телефон').length,'Форма реквизитов ещё не загрузилась. Продолжите после загрузки страницы.');
   const address=field('Юридический адрес'),bank=field('Банковский счёт'),phoneRoot=field('Контактный телефон'),input=phoneRoot.querySelector('input');
   const taxFields=fields('Тип налогоплательщика');
   if(taxFields.length>1)fail('Неоднозначное поле типа налогоплательщика.');
   const expectedTax=packet.data.taxpayer_type?.trim()||'';
   const taxReady=!taxFields.length||!expectedTax||norm(selected(taxFields[0]))===norm(expectedTax);
   const ready=accountMatches(selected(address),'address')&&accountMatches(selected(bank),'bank')&&phone(input?.value)===phone(packet.data.contact_phone)&&taxReady;
   if(action==='probe'){if(ready)requireAccountFields();return result('account',ready?'save_account':'fill_account','Реквизиты организации.');}
   if(action==='fill_account'){
    await choose(address,packet.data.legal_address,value=>accountMatches(value,'address'),{search:false});
    await choose(bank,packet.data.bank_account,value=>accountMatches(value,'bank'),{search:false});
    if(taxFields.length&&expectedTax)await choose(taxFields[0],expectedTax,value=>norm(value)===norm(expectedTax),{search:false});
    if(number&&input.value.trim()&&phone(input.value)!==phone(packet.data.contact_phone))fail('В существующем черновике другой телефон. Проверьте его вручную.');
    setInput(input,packet.data.contact_phone);
    return result('account_ready',null,'Реквизиты подставлены.');
   }
   if(action!=='save_account'||!ready)fail('Реквизиты не готовы к сохранению.');
   if(context.applicationNumber&&!number)fail('Откройте ранее созданный черновик, а не новую форму заявки.');
   requireAccountFields();
   const consortium=[...document.querySelectorAll('label')].find(el=>visible(el)&&/консорциум/i.test(text(el)));
   if(consortium?.querySelector('input:checked'))fail('Подготовка заявки консорциума пока не поддерживается.');
   mutationStarted=true;click(button('Далее'));
   await wait(()=>{const u=new URL(location.href);return u.pathname===apply&&/^\d+$/.test(u.searchParams.get('id')||'')&&u.searchParams.get('step')==='2';},'Создание черновика не подтверждено. Не повторяйте создание: проверьте «Мои заявки» или откройте объявление заново.');
   return result('draft_created',null,'Черновик на портале создан.',{number:new URL(location.href).searchParams.get('id')});
  }
  if(!number)fail('Номер черновика не подтверждён.');
  if(step===2){
   const active=[...document.querySelectorAll('[role=tab][aria-selected=true]')].find(visible);
   const viewing=active&&norm(text(active)).startsWith('просмотр выбранных');
   if(action==='probe'){
    const rows=viewing?selectedRows():[];
    const ready=rows.length===1&&lotTokens(text(rows[0])).length===1&&lotTokens(text(rows[0]))[0]===packet.lot_number;
    return result('lots',ready?'advance_lots':'add_lot','Выбор лота.',{lotConfirmed:Boolean(ready)});
   }
   if(action==='add_lot'){
    await openTab('Просмотр выбранных');
    const existing=selectedRows();
    if(existing.length){
     if(existing.length===1&&lotTokens(text(existing[0])).length===1&&lotTokens(text(existing[0]))[0]===packet.lot_number)return result('lot_selected',null,'Нужный лот уже добавлен.',{lotConfirmed:true});
     fail('В черновике уже есть другие лоты. Проверьте состав вручную; расширение ничего не удаляет.');
    }
    await openTab('Лоты');
    const boxes=[...document.querySelectorAll('input[type=checkbox]')].filter(el=>visible(el)&&!el.disabled);
    const candidates=boxes.filter(box=>{
     for(let parent=box.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
      const numbers=lotTokens(text(parent));if(numbers.length===1&&numbers[0]===packet.lot_number&&parent.querySelectorAll('input[type=checkbox]').length===1)return true;
      if(numbers.length>1)return false;
     }return false;
    });
    if(candidates.length!==1)fail('Нужный лот не найден однозначно. Найдите его в списке вручную.');
    if(boxes.some(box=>box.checked&&box!==candidates[0]))fail('Выбраны другие лоты. Проверьте выбор вручную.');
    if(!candidates[0].checked)click(candidates[0]);
    mutationStarted=true;click(button('Добавить выбранные'));
    await new Promise(resolve=>setTimeout(resolve,500));
    if(restrictions())fail(restrictions().slice(0,350));
    await openTab('Просмотр выбранных');
    await wait(()=>exactLotRows().length===1,'Добавление лота не подтвердилось. Проверьте ограничения участия на портале.');
    if(selectedRows().length!==1)fail('В составе заявки есть другие лоты. Проверьте их вручную.');
    return result('lot_selected',null,'Нужный лот добавлен.',{lotConfirmed:true});
   }
   if(action!=='advance_lots'||!viewing||selectedRows().length!==1||exactLotRows().length!==1)fail('Состав заявки не подтверждён.');
   mutationStarted=true;click(button('Далее'));
   await wait(()=>new URL(location.href).searchParams.get('step')==='3','Переход к документам не подтверждён.');
   return result('documents_opened',null,'Открыты документы заявки.',{lotConfirmed:true});
  }
  if(step===3){
   const descriptionLinks=()=>actions().filter(el=>norm(text(el)).startsWith('описание товара')&&(lotTokens(text(el)).includes(packet.lot_number)||context.lotConfirmed));
   await wait(()=>descriptionLinks().length,'Документы ещё не загрузились или «Описание товара» нужного лота не найдено. Продолжите после загрузки страницы.');
   const links=descriptionLinks();
   if(links.length!==1)fail('Откройте «Описание товара» для нужного лота вручную, затем продолжите заполнение.');
   if(action==='probe')return result('documents','open_description','Готов переход к описанию товара.');
   if(action!=='open_description')fail('Действие не разрешено.');
   click(links[0]);await wait(()=>location.pathname.startsWith(apply+'/product-description'),'Переход к описанию не подтверждён.');
   return result('description_opened',null,'Открыто описание товара.');
  }
  fail('Шаг не поддерживается.');
 }catch(error){return {blocked:true,uncertain:mutationStarted,message:error.message||'Не удалось проверить форму. Действия остановлены.'};}
}

if(typeof module!=='undefined')module.exports={validateDraftPacket,runDraftStep};

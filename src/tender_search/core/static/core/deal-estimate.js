// Calculations and public tariff lookup only. Never submits a bid or an order.
(()=>{
 const root=document.getElementById('deal-estimate');if(!root)return;
 const form=root.querySelector('form'),preparation=document.getElementById('preparation-form'),status=root.querySelector('#deal-estimate-status'),result=root.querySelector('#deal-estimate-result'),button=form.querySelector('button[type=submit]');
 let saved=JSON.parse(document.getElementById('saved-deal-estimate')?.textContent||'null');
 const element=name=>form.elements.namedItem(name);
 const add=(tag,text,parent)=>{const el=document.createElement(tag);el.textContent=text;parent.append(el);return el;};
 const money=value=>Number(value).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' ₸';
 const commercial=['product_name','unit_gross','sku','procurement_mode','purchase_unit_gross','purchase_vat_rate','purchase_vat_credit'];
 const delivery=['delivery_mode','delivery_gross','delivery_vat_rate','delivery_vat_credit','origin','destination','weight_kg','volume_m3','packages','cargo_type','delivery_option','local_delivery_gross'];
 function locks(){for(const name of commercial)if(element(name))element(name).disabled=!!saved?.commercial_locked;for(const name of delivery)if(element(name))element(name).disabled=!!saved?.delivery_locked;}
 const invalidate=()=>{if(result.childNodes.length){result.replaceChildren();document.dispatchEvent(new Event('preparation-estimate-invalidated'));status.textContent='Данные изменились. Рассчитайте заново.';}};
 const fingerprint=()=>JSON.stringify([Array.from(new FormData(form)),preparation?.elements.namedItem('unit_gross')?.value,window.tenderObligations?.read()]);
 document.addEventListener('obligation-costs-changed',invalidate);
 form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
 preparation?.elements.namedItem('unit_gross')?.addEventListener('input',invalidate);
 function mode(){const p1=element('procurement_mode').value==='p1';if(p1)element('delivery_mode').value='jet';element('delivery_mode').disabled=p1;const jet=element('delivery_mode').value==='jet';root.querySelector('[data-jet-fields]').hidden=!jet;root.querySelector('[data-manual-delivery]').hidden=jet;root.querySelector('[data-manual-origin]').hidden=p1;root.querySelector('[data-manual-purchase]').hidden=p1;root.querySelectorAll('[data-p1-fields]').forEach(el=>el.hidden=!p1);element('sku').required=p1;element('purchase_unit_gross').required=!p1;locks();}
 element('delivery_mode').addEventListener('change',mode);element('procurement_mode').addEventListener('change',mode);mode();
 let skuRequest=0;
 element('sku').addEventListener('input',()=>skuRequest++);
 document.addEventListener('over-product-selected',async event=>{
  if(saved?.commercial_locked)return;
  if(event.detail.preserve&&element('sku').value)return;
  const revision=++skuRequest;element('sku').value='';invalidate();
  try{const response=await fetch(root.dataset.url+'?'+new URLSearchParams({product_url:event.detail.url}),{credentials:'same-origin',signal:AbortSignal.timeout(30000)});if(response.redirected)throw Error('Войдите заново.');const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось определить SKU.');if(revision===skuRequest){element('sku').value=data.sku;status.textContent='SKU выбранного товара: '+data.sku+'. Введите параметры отправки и сравните склады.';}}
  catch(error){if(revision===skuRequest)status.textContent=error.message;}
 });
 async function request(data){
  const response=await fetch(root.dataset.url,{method:data?'POST':'GET',credentials:'same-origin',signal:AbortSignal.timeout(90000),headers:data?{'Content-Type':'application/json','X-CSRFToken':preparation.elements.namedItem('csrfmiddlewaretoken').value}:{},body:data?JSON.stringify(data):undefined});
  if(response.redirected)throw Error('Войдите в приложение заново.');
  const body=await response.json();if(!response.ok)throw Error(body.error||'Расчёт недоступен.');return body;
 }
 function fill(values){for(const [name,value] of Object.entries(values||{})){const field=element(name);if(!field||typeof value==='object')continue;const text=typeof value==='boolean'?(value?'yes':'no'):String(value??'');if(field.tagName==='SELECT'&&text&&![...field.options].some(option=>option.value===text))field.add(new Option(text,text));field.value=text;}}
 fill(saved?.inputs||{product_name:preparation?.elements.namedItem('product_name')?.value||'',unit_gross:preparation?.elements.namedItem('unit_gross')?.value||''});mode();
 function syncPrepared(){
  if(!saved?.commercial_locked){for(const name of ['product_name','unit_gross']){const source=preparation?.elements.namedItem(name),target=element(name);if(source&&target)target.value=source.value;}}
  const title=root.querySelector('[data-deal-product]'),sale=root.querySelector('[data-deal-sale]');
  if(title)title.textContent=element('product_name').value||'Выберите товар в подготовке заявки';
  if(sale)sale.textContent=element('unit_gross').value?money(element('unit_gross').value.replace(/\s/g,'').replace(',','.')):'Укажите цену в подготовке заявки';
 }
 syncPrepared();
 const sourceDiffers=!!saved&&!saved.commercial_locked&&(String(saved.inputs?.product_name??'')!==element('product_name').value||Number(saved.inputs?.unit_gross)!==Number(element('unit_gross').value.replace(/\s/g,'').replace(',','.')));
 if(!saved?.delivery_locked)element('delivery_option').value='door_to_door';
 for(const name of ['product_name','unit_gross'])preparation?.elements.namedItem(name)?.addEventListener('input',()=>{syncPrepared();invalidate();});

 if(!saved?.delivery_locked)request().then(data=>{
  for(const name of ['origin','destination'])for(const city of data.cities)if(![...element(name).options].some(option=>option.value===city))element(name).add(new Option(city,city));
  for(const cargo of data.cargo_types)if(![...element('cargo_type').options].some(option=>option.value===cargo))element('cargo_type').add(new Option(cargo,cargo));
  if(!saved&&data.destination)element('destination').value=data.destination;
  root.querySelector('#deal-location').textContent=data.locations.filter(Boolean).length?'Место поставки из лота: '+data.locations.join('; '):'Место поставки в сохранённых данных не уточнено. Проверьте техспецификацию.';
 }).catch(e=>{root.querySelector('#deal-location').textContent=e.message;});
 else root.querySelector('#deal-location').textContent='Доставка зафиксирована с этапа «Оплата». Показан сохранённый тариф.';
 function renderCalculation(response,parent){
   const r=response.result;
   if(response.procurement_plan?.allocations?.length){add('h4','Сохранённая закупка · '+response.procurement_plan.origin_city,parent);for(const part of response.procurement_plan.allocations)add('p',part.warehouse+': '+part.quantity+' × '+money(part.purchase_unit_gross),parent);}
   const heading=r.economic_profit===null?'Прибыль до налога на доход':'Оценка прибыли после налога и стоимости заморозки';
   add('h3',heading+': '+money(r.economic_profit??r.profit_before_tax),parent);
   add('p',(r.margin_pct===null?'Операционная маржа: ':'Маржа с учётом выбранных расходов: ')+(r.margin_pct??r.operating_margin_pct)+'%',parent);
   const dl=add('dl','',parent);
   if(r.additional_obligations?.length){add('h4','Дополнительные обязательства · за весь лот',parent);const list=add('ul','',parent);for(const entry of r.additional_obligations)add('li',entry.label+': '+money(entry.amount),list);add('p','Итого по обязательствам: '+money(r.obligations_total)+'. Включено в расходы ниже.',parent);}
   for(const [title,value] of [['Выручка без НДС',r.revenue_net],['Себестоимость закупки после зачёта подтверждённого НДС',r.purchase_net],['Доставка всего',response.delivery_gross],['Другие расходы',r.other_costs],['Сальдо НДС · отдельно, повторно из прибыли не вычитается',r.vat_balance],['Возвратные обеспечения · не расход',r.refundable_security],['Стоимость заморозки',r.freeze_cost],['Потребность в деньгах · закупка, доставка, расходы, обеспечения',r.cash_required]]){add('dt',title,dl);add('dd',money(value),dl);}
   if(r.income_tax!==null){add('dt','Налог по указанному сценарию',dl);add('dd',money(r.income_tax),dl);}
   if(response.freight?.local_manual)add('p','Доставка внутри города: сумма указана вами.',parent);
   else if(response.freight)add('p','Jet Logistic: '+response.freight.days_min+'–'+response.freight.days_max+' рабочих дней. Тариф предварительный; НДС и адресные доплаты требуют подтверждения.',parent);
 }
 function renderResponse(response){
   if(response.comparison){
    const c=response.comparison;
    add('h3',c.product.name+' · SKU '+c.product.sku,result);
    add('p','Цены и остатки p1 на '+new Date(c.catalog_checked_at).toLocaleString('ru-RU')+'. Справочник обновляется раз в час; перед закупкой подтвердите наличие.',result);
    if(!c.comparison_complete)add('p','Сравнение неполное: часть складов или тарифов требует проверки.',result).className='notice';
    for(const row of c.unavailable)add('p',row.warehouse+' · '+row.origin_city+': '+row.reason,result);
    if(c.unknown_warehouses.length)add('p','Не задан город: '+c.unknown_warehouses.join(', '),result);
    if(c.invalid_entries)add('p','Не удалось разобрать предложений p1: '+c.invalid_entries,result);
    if(!c.offers.length){status.textContent='Нет рассчитанных предложений с достаточным остатком. Проверьте маршрут и остатки.';return;}
    add('p','В каждом городе сначала берём остаток с минимальной ценой, затем добираем по возрастанию. Варианты отсортированы по общей закупке с НДС. Доставка учтена отдельно.',result);
    const wrap=add('div','',result);wrap.className='deal-comparison-scroll';const table=add('table','',wrap);table.className='deal-comparison';const head=add('tr','',add('thead','',table));
    for(const title of ['Склады / город','Остаток в городе','Средний закуп за ед. с НДС','Закуп всего с НДС','Доставка','Закуп + доставка','Прибыль / маржа',''])add('th',title,head);
    const body=add('tbody','',table),detail=add('div','',result);
    const choose=(offer,row)=>{for(const sibling of body.children)sibling.classList.toggle('selected',sibling===row);detail.replaceChildren();add('h3','Закупка в городе '+offer.origin_city,detail);for(const part of offer.allocations)add('p',part.warehouse+': '+part.quantity+' × '+money(part.purchase_unit_gross)+' = '+money(part.purchase_gross)+' с НДС',detail);if(offer.multiple_pickups)add('p','Сборка с нескольких складов: тариф рассчитан на одну общую отправку. Стоимость нескольких заборов или консолидации нужно подтвердить и добавить в другие расходы.',detail).className='notice';renderCalculation(offer,detail);};
    for(const offer of c.offers){const r=offer.result,row=add('tr','',body);for(const value of [offer.warehouse+' · '+offer.origin_city,offer.stock,money(offer.purchase_unit_gross),money(r.purchase_gross),money(offer.delivery_gross),money(offer.landed_gross),money(r.economic_profit??r.profit_before_tax)+' / '+(r.margin_pct??r.operating_margin_pct)+'%'])add('td',value,row);const select=add('button','Выбрать',add('td','',row));select.type='button';select.className='outline';select.disabled=!!saved?.commercial_locked;select.addEventListener('click',async()=>{select.disabled=true;try{const reply=await request({action:'select_offer',index:c.offers.indexOf(offer),version:saved.version});saved=reply.saved;choose(offer,row);status.textContent='Выбранная закупка сохранена.';}catch(error){status.textContent=error.message;}finally{select.disabled=!!saved?.commercial_locked;}});}
    const selected=response.saved?.selected_offer??saved?.selected_offer;
    if(Number.isInteger(selected)&&c.offers[selected])choose(c.offers[selected],body.children[selected]);
    else if(c.minimum_purchase&&Number(c.minimum_purchase.purchase_total_gross)<Number(c.offers[0].purchase_total_gross))add('p','Самая дешёвая закупка: '+c.minimum_purchase.warehouse+' · '+c.minimum_purchase.origin_city+'. Доставка не рассчитана — выберите рассчитанный вариант явно.',detail).className='notice';else choose(c.offers[0],body.firstElementChild);
   }else renderCalculation(response,result);
   const r=response.result||response.comparison.offers[0].result;
   document.dispatchEvent(new Event('preparation-estimate-complete'));
   status.textContent=(saved?'Расчёт сохранён. ':'')+(r.income_tax===null?'Расчёт предварительный. Налог на доход не задан; прибыль показана до этого налога.':'Сценарий рассчитан по введённым расходам и ставке налога. Это оценка сделки.');

 }
 if(saved){renderResponse({...saved.snapshot,saved});locks();if(sourceDiffers)invalidate();}
 form.addEventListener('submit',async e=>{
  e.preventDefault();button.disabled=true;result.replaceChildren();status.textContent='Рассчитываем стоимость и прибыль…';
  const original=fingerprint(),data=Object.fromEntries(new FormData(form));data.delivery_mode=element('delivery_mode').value;data.unit_gross=element('unit_gross').value||'';if(saved)data.estimate_version=saved.version;for(const name of [...commercial,...delivery])if(element(name)?.disabled&&saved?.inputs&&name in saved.inputs)data[name]=saved.inputs[name];data.purchase_vat_credit=data.purchase_vat_credit===true||data.purchase_vat_credit==='yes';if(window.tenderObligations)data.obligations=window.tenderObligations.read();
  try{
   const response=await request(data);
   if(response.saved)saved=response.saved;
   if(fingerprint()!==original)throw Error('Данные изменились во время расчёта. Рассчитайте заново.');
   renderResponse(response);
  }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
 });
})();

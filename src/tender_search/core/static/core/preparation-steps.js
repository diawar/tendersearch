// UI guidance only. Authoritative validation and workflow transitions remain on the server.
(()=>{
 const guide=document.getElementById('preparation-guide'),form=document.getElementById('preparation-form');if(!guide||!form)return;
 const field=name=>form.elements.namedItem(name),launch=document.getElementById('prepare-with-extension');
 let dirty=false,finance=false;
 const names=['product_name','manufacturer','country'],requisites=['legal_address','bank_account','contact_phone','taxpayer_type'];
 const label=name=>document.querySelector('label[for="'+field(name)?.id+'"]')?.textContent.replace(/:\s*$/,'')||name;
 const missing=list=>list.filter(name=>field(name)&&(!field(name).value.trim()||!field(name).validity.valid)).map(label);
 function update(){
  const product=missing(names);if(field('unit_gross'))product.push(...missing(['unit_gross']));
  if(field('confirm_discount')?.required&&!field('confirm_discount').checked)product.push('Подтвердите снижение цены на 10% и более');
  const company=missing(requisites),saved=guide.dataset.saved==='true'&&!dirty;
  if(guide.dataset.binReady!=='true')company.unshift('БИН организации в настройках');
  const spec=!!document.querySelector('#preparation-specification details');
  const draft=guide.dataset.draftReady==='true',submitted=guide.dataset.submitted==='true';
  const stage=guide.dataset.workflow,active=stage==='preparing';
  let current=!spec?1:product.length?2:company.length||!saved?3:!draft?5:6;
  if(!active)current=6;
  const text={1:['Прочитайте требования заказчика','Дождитесь загрузки техспецификации и проверьте характеристики и дополнительные обязательства.'],2:['Заполните товар и нашу цену','Осталось: '+product.join('; ')+'.'],3:['Проверьте реквизиты и сохраните подготовку',company.length?'Осталось: '+company.join('; ')+'.':dirty?'Есть несохранённые изменения. Сохраните товар, цену и реквизиты.':'Данные заполнены. Сохраните подготовку.'],5:['Создайте черновик на zakup.gov.kz','Подготовка сохранена. Передайте её в расширение и запустите заполнение черновика.'],6:[stage==='waiting_results'?'Ожидаем итоги':submitted?'Подача подтверждена':'Завершите подачу на Госзакупе',stage==='waiting_results'?'Приём завершён. Проверка участия и победителя по БИН продолжается.':submitted?'После окончания срока — «Ожидаем итоги».':'Черновик не является заявкой. Цена, обеспечение, подпись и отправка выполняются на портале.']};
  const later={applied:'Подали заявку',contract:'Заключение договора',delivery:'Поставка',payment:'Оплата',won:'Выиграли',lost:'Проиграли',archived:'Архив'};
  if(!active&&later[stage])text[6]=['Текущий этап: '+later[stage],'Просмотр сохранённой подготовки. Дальнейшее движение лота — в рабочем пространстве.'];
  if(current===5&&launch?.dataset.launchReady!=='true')text[5]=['Проверьте готовность черновика',document.querySelector('[data-launch-missing]')?.textContent||'Запуск пока недоступен.'];
  guide.querySelector('[data-current-step]').textContent=text[current][0];guide.querySelector('[data-current-help]').textContent=text[current][1];
  guide.querySelector('[data-next-step]').href='#prep-step-'+current;
  const states={1:spec?'Текст загружен · проверьте':'Нужна техспецификация',2:product.length?'Осталось полей: '+product.length:'Заполнено',3:company.length?'Осталось полей: '+company.length:saved?'Сохранено':'Нужно сохранить',4:finance?'Расчёт выполнен':'Необязательно для черновика',5:draft?'Заполнение подтверждено':'Черновик не подтверждён',6:stage==='waiting_results'?'Проверяем по БИН':submitted?'Подача подтверждена':'Подача не подтверждена'};
  for(const link of guide.querySelectorAll('[data-step]')){const n=Number(link.dataset.step);link.dataset.state=n===current?'current':({1:spec,2:!product.length,3:saved&&!company.length,4:finance,5:draft,6:submitted}[n]?'done':'pending');if(n===current)link.setAttribute('aria-current','step');else link.removeAttribute('aria-current');link.querySelector('small').textContent=states[n];}
  document.querySelector('[data-product-missing]').textContent=product.length?'Осталось: '+product.join('; ')+'.':'Товар и цена заполнены. Проверьте соответствие техспецификации.';
  const companyOutput=document.querySelector('[data-company-missing]');companyOutput.textContent=company.length?'Осталось: '+company.join('; ')+'.':'Реквизиты заполнены. Они должны совпадать с данными Госзакупа.';
  const save=document.getElementById('save-preparation');if(save)save.disabled=!!product.length||!!company.length;
  if(launch){launch.disabled=launch.dataset.launchReady!=='true'||dirty||!!product.length||!!company.length;if(dirty)document.querySelector('[data-launch-missing]').textContent='Данные изменились. Сначала сохраните подготовку.';}
  if(dirty){const status=document.querySelector('[data-preparation-save-state]');if(status)status.textContent='Есть несохранённые изменения';}
 }
 form.addEventListener('input',()=>{dirty=true;queueMicrotask(update);});form.addEventListener('change',()=>{dirty=true;queueMicrotask(update);});
 document.addEventListener('htmx:afterSwap',update);
 document.addEventListener('preparation-estimate-complete',()=>{finance=true;update();});
 document.addEventListener('preparation-estimate-invalidated',()=>{finance=false;update();});
 document.addEventListener('preparation-launch-idle',update);
 document.addEventListener('obligation-costs-changed',()=>{finance=false;update();});
 update();
})();

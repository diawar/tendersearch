// Local specification hints and personal cost estimates. No AI or portal writes.
(()=>{
 const root=document.getElementById('obligation-costs');if(!root)return;
 const rows=root.querySelector('[data-obligation-rows]'),status=root.querySelector('[data-obligation-status]'),editable=root.dataset.editable==='true';
 const add=(tag,text,parent)=>{const el=document.createElement(tag);if(text)el.textContent=text;parent.append(el);return el;};
 const entries=()=>Array.from(rows.children,row=>({label:row.querySelector('[data-label]').value,amount:row.querySelector('[data-amount]').value,evidence:row.dataset.evidence||'',document:row.dataset.document||''}));
 const notify=()=>document.dispatchEvent(new CustomEvent('obligation-costs-changed'));
 function total(){let cents=0,unknown=0;for(const row of entries()){const value=row.amount.trim().replace(/\s/g,'').replace(',','.');if(!/^\d+(?:\.\d{1,2})?$/.test(value)){unknown++;continue;}cents+=Math.round(Number(value)*100);}root.querySelector('[data-obligation-total]').textContent=(cents/100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' ₸'+(unknown?' · не оценено: '+unknown:'');}
 function changed(){total();status.textContent='Расходы изменены. Для сохранения нажмите «Рассчитать и сохранить».';notify();}
 function row(entry={}){
  if(rows.children.length>=30){status.textContent='Можно добавить до 30 расходов.';return;}
  const wrap=add('div','',rows);wrap.className='obligation-row';wrap.dataset.evidence=entry.evidence||'';wrap.dataset.document=entry.document||'';
  const grid=add('div','',wrap);grid.className='obligation-grid';
  const nameLabel=add('label','Расход',grid),name=add('input','',nameLabel);name.dataset.label='';name.value=entry.label||'';name.maxLength=250;name.required=true;name.disabled=!editable;
  const amountLabel=add('label','Стоимость за весь лот, ₸',grid),amount=add('input','',amountLabel);amount.dataset.amount='';amount.value=entry.amount??'';amount.inputMode='decimal';amount.placeholder='Ещё не оценено';amount.disabled=!editable;
  if(editable){const remove=add('button','Удалить',grid);remove.type='button';remove.className='outline';remove.addEventListener('click',()=>{wrap.remove();changed();});}
  if(entry.evidence){const evidence=add('details','',wrap);add('summary','Выдержка · '+(entry.document||'техспецификация'),evidence);add('blockquote',entry.evidence,evidence);}
 }
 JSON.parse(document.getElementById('obligation-costs-data').textContent).forEach(row);total();
 window.tenderObligations={read:entries};
 root.addEventListener('input',changed);
 root.querySelector('[data-obligation-add]')?.addEventListener('click',()=>{row();changed();rows.lastElementChild?.querySelector('input').focus();});
})();

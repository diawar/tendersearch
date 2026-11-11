(function(root){
 'use strict';
 const round=(value,denominator)=>(value+denominator/2n)/denominator;
 function parseMoney(text){
  const value=String(text).replace(/[\s\u00a0\u202f]/g,'').replace(',','.');
  if(!/^\d{1,20}(?:\.\d{0,2})?$/.test(value))return null;
  const [whole,fraction='']=value.split('.');const cents=BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
  return cents>0n?cents:null;
 }
 function calculate(gross,rate,quantity){
  if(gross===null||!['0','5','12','16'].includes(String(rate)))return null;
  const percent=BigInt(rate),net=round(gross*100n,100n+percent);
  const result={unit_net:net,unit_vat:gross-net};
  if(quantity&&/^\d{1,13}(?:\.\d{1,6})?$/.test(quantity)){
   const [whole,fraction='']=quantity.split('.'),scale=10n**BigInt(fraction.length),count=BigInt(whole+fraction);
   if(count>0n&&count<=1000000000000n*scale){
    result.total_net=round(net*count,scale);
    result.total_vat=round(result.total_net*percent,100n);
    result.total_gross=result.total_net+result.total_vat;
    result.desired_total=round(gross*count,scale);
    result.entered_total_gross=result.desired_total;
    result.rounding_difference=result.total_gross-result.desired_total;
   }
  }
  return result;
 }
 function formatMoney(cents){
  if(typeof cents!=='bigint')return '—';
  const sign=cents<0n?'−':'';const value=cents<0n?-cents:cents;
  return sign+(value/100n).toLocaleString('ru-RU')+','+(value%100n).toString().padStart(2,'0');
 }
 function checkBudget(result,unitBudget,totalBudget){
  const exceeds=!!(result&&unitBudget!==null&&totalBudget!==null&&(result.unit_net>unitBudget||result.total_net>totalBudget));
  const discounted=!!(result&&totalBudget!==null&&result.total_net!==undefined&&result.total_net*100n<=totalBudget*90n&&!exceeds);
  return {exceeds,discounted};
 }
 const api={parseMoney,calculate,formatMoney,checkBudget};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 if(typeof document==='undefined')return;
 function update(form){
  const gross=parseMoney(form.elements.namedItem('unit_gross').value);
  const result=calculate(gross,form.elements.namedItem('vat_rate').value,form.dataset.quantity);
  form.querySelectorAll('[data-price-output]').forEach(el=>{el.textContent=formatMoney(result?.[el.dataset.priceOutput]);});
  const unitBudget=parseMoney(form.dataset.budgetUnit||'');
  const totalBudget=parseMoney(form.dataset.budgetTotal||'');
  const {exceeds,discounted}=checkBudget(result,unitBudget,totalBudget);
  const budgetError=form.querySelector('[data-price-budget-error]');if(budgetError)budgetError.hidden=!exceeds;
  const discount=form.querySelector('[data-price-discount]');if(discount)discount.hidden=!discounted&&!discount.querySelector('.errorlist');
  const confirm=form.elements.namedItem('confirm_discount');if(confirm){confirm.required=discounted;if(!discounted)confirm.checked=false;}
  const input=form.elements.namedItem('unit_gross');input.setCustomValidity(exceeds?'Наша цена без НДС превышает бюджет.':'');
  const rounding=form.querySelector('[data-price-rounding]');if(rounding)rounding.hidden=!result?.rounding_difference;
 }
 document.addEventListener('input',event=>{if(event.target.form?.matches('.lot-price-form')){if(event.target.name==='unit_gross'){const confirm=event.target.form.elements.namedItem('confirm_discount');if(confirm)confirm.checked=false;}update(event.target.form);}});
 document.addEventListener('change',event=>{if(event.target.form?.matches('.lot-price-form'))update(event.target.form);});
 const init=()=>document.querySelectorAll('.lot-price-form').forEach(update);
 document.addEventListener('htmx:afterSwap',init);init();
})(globalThis);

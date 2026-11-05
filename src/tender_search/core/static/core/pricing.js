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
 const api={parseMoney,calculate,formatMoney};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 if(typeof document==='undefined')return;
 function update(form){
  const gross=parseMoney(form.elements.namedItem('unit_gross').value);
  const result=calculate(gross,form.elements.namedItem('vat_rate').value,form.dataset.quantity);
  form.querySelectorAll('[data-price-output]').forEach(el=>{el.textContent=formatMoney(result?.[el.dataset.priceOutput]);});
  const rounding=form.querySelector('[data-price-rounding]');if(rounding)rounding.hidden=!result?.rounding_difference;
 }
 document.addEventListener('input',event=>{if(event.target.form?.matches('.lot-price-form'))update(event.target.form);});
 document.addEventListener('change',event=>{if(event.target.form?.matches('.lot-price-form'))update(event.target.form);});
 const init=()=>document.querySelectorAll('.lot-price-form').forEach(update);
 document.addEventListener('htmx:afterSwap',init);init();
})(globalThis);

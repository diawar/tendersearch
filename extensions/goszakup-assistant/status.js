/* Read-only, lot-scoped status extraction. No requests, cookies or form changes. */
function statusTarget(values){
 const target=Object.fromEntries(['company_bin','announcement_number','lot_number'].map(key=>[key,String(values[key]||'').trim().replace(/[–—]/g,'-')]));
 if(!/^\d{12}$/.test(target.company_bin))throw Error('Укажите БИН организации: 12 цифр.');
 if(!/^\d{6,}-\d+$/.test(target.announcement_number))throw Error('Укажите полный номер объявления, например 17896678-1.');
 if(!/^\d+-[А-Яа-яA-Za-z0-9]+$/.test(target.lot_number))throw Error('Укажите полный номер лота, например 88391354-ЗЦП2.');
 return target;
}
function reportTransferUrl(address,report){
 const base=new URL(address);
 const local=['localhost','127.0.0.1','[::1]'].includes(base.hostname);
 if((base.protocol!=='https:'&&!(local&&base.protocol==='http:'))||base.username||base.password||base.search||base.hash||base.pathname!=='/')throw Error('Укажите адрес приложения: HTTPS или http://localhost:8000, без пути и параметров.');
 if(!report||report.schema!=='goszakup-status-v1')throw Error('Сначала выполните проверку.');
 const data=JSON.stringify(report);
 if(data.length>12000)throw Error('Отчёт слишком большой.');
 return base.origin+'/settings/extension/report/#'+encodeURIComponent(data);
}
function readPortalStatus(packet){
 const origin=new URL(location.href);
 if(origin.origin!=='https://zakup.gov.kz'||!origin.pathname.startsWith('/cabinet/'))return {error:'Неверный сайт'};
 const visible=el=>Boolean(el.getClientRects().length);
 const norm=s=>String(s||'').toLowerCase().replace(/\s+/g,' ').trim();
 const bins=[...new Set([...document.querySelectorAll('header,[role=banner],.ant-layout-header')].filter(visible)
  .flatMap(el=>[...(el.innerText||el.textContent||'').matchAll(/БИН\s*:?\s*(\d{12})/g)].map(m=>m[1])))];
 if(bins.length!==1||bins[0]!==packet.company_bin)return {error:'БИН организации в кабинете не подтверждён'};
 const base={schema:'goszakup-status-v1',company_bin:packet.company_bin,announcement_number:packet.announcement_number,lot_number:packet.lot_number,observed_at:new Date().toISOString(),state:'unknown'};
 const matches=[];
 const tableHeaders=table=>[...table.querySelectorAll('thead th')].map(el=>norm(el.innerText));
 // Scrolling portal lists render the header and body in separate tables.
 // Pair only an unambiguous pair in a shared local container, never across the page.
 const headersFor=table=>{
  const own=tableHeaders(table);
  if(own.length)return own;
  for(let scope=table.parentElement;scope&&scope!==document.body;scope=scope.parentElement){
   const tables=[...scope.querySelectorAll('table')].filter(visible);
   const heads=tables.filter(t=>tableHeaders(t).length);
   const bodies=tables.filter(t=>t.querySelectorAll('tbody tr').length);
   if(heads.length>1||bodies.length>1)return [];
   if(heads.length===1&&bodies.length===1&&bodies[0]===table)return tableHeaders(heads[0]);
  }
  return [];
 };
 for(const table of document.querySelectorAll('table')){
  if(!visible(table))continue;
  const headers=headersFor(table);
  const statusIndex=headers.indexOf('статус заявки');
  const numberIndex=headers.indexOf('№ заявки');
  const announcementIndex=headers.indexOf('№ объявления');
  const lotIndex=headers.findIndex(x=>x==='лоты в заявке'||x==='лот в заявке');
  if([statusIndex,numberIndex,announcementIndex,lotIndex].some(x=>x<0))continue;
  for(const row of table.querySelectorAll('tbody tr')){
   if(!visible(row))continue;
   const cells=[...row.querySelectorAll(':scope > td')];
   if(cells.length<=Math.max(statusIndex,numberIndex,announcementIndex,lotIndex))continue;
   const announcements=cells[announcementIndex].innerText.match(/\d{6,}-\d+/g)||[];
   const lots=cells[lotIndex].innerText.replace(/[–—]/g,'-').split(/[\s,;]+/);
   if(!announcements.includes(packet.announcement_number)||!lots.includes(packet.lot_number))continue;
   const status=norm(cells[statusIndex].innerText);
   const state=status==='проект'?'draft':(['подана','подано','заявка подана'].includes(status)?'submitted':(status==='завершена'?'completed':'unknown'));
   const number=cells[numberIndex].innerText.trim();
   const links=[...cells[numberIndex].querySelectorAll('a[href]')].map(a=>new URL(a.href,location.href)).filter(u=>u.origin==='https://zakup.gov.kz'&&/^\/cabinet\/(?:applications?|purchase\/my-applications)\/\d+\/?$/.test(u.pathname)&&!u.search&&!u.hash);
   const unique=[...new Set(links.map(u=>u.href))];
   if(!unique.length&&/^\d{1,30}$/.test(number)&&[...cells[numberIndex].querySelectorAll('button')].some(b=>visible(b)&&b.innerText.trim()===number)){
    unique.push('https://zakup.gov.kz/cabinet/purchase/my-applications/'+number);
   }
   if(state==='unknown'||!/^\d{1,30}$/.test(number)||unique.length!==1){matches.push(base);continue;}
   matches.push({...base,state,number,url:unique[0]});
  }
 }
 // No matching row is not evidence that a draft is absent (filters/pagination).
 if(matches.length!==1)return base;
 return matches[0];
}
if(typeof module!=='undefined')module.exports={readPortalStatus,statusTarget,reportTransferUrl};

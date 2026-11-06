const assert=require('node:assert/strict');
const {readPortalStatus}=require('./status.js');
const packet={company_bin:'241040001415',announcement_number:'17921577-1',lot_number:'88524385-ЗЦП1'};
global.location={href:'https://zakup.gov.kz/cabinet/applications'};
function setup(status='Проект', options={}){
 const texts=['123','17921577-1','88524385-ЗЦП1',status];
 if(options.otherLot)texts[2]='999-ЗЦП1';
 const link={href:'https://zakup.gov.kz/cabinet/application/123'};
 const cells=texts.map((innerText,index)=>({innerText,querySelectorAll:selector=>index===0?(options.button?(selector==='button'?[{innerText:'123',getClientRects:()=>[1]}]:[]):(selector==='a[href]'?[link]:[])):[]}));
 const row={getClientRects:()=>[1],querySelectorAll:()=>cells};
 const headers=['№ заявки','№ объявления','Лоты в заявке','Статус заявки'].map(innerText=>({innerText}));
 const table={getClientRects:()=>[1],querySelectorAll:s=>s==='thead th'?headers:(options.duplicate?[row,row]:[row])};
 let tables=[table];
 const body={innerText:'БИН: '+(options.wrongBin?'000000000000':packet.company_bin)};
 if(options.split){
  const head={getClientRects:()=>[1],querySelectorAll:s=>s==='thead th'?headers:[]};
  const rows={getClientRects:()=>[1],querySelectorAll:s=>s==='tbody tr'?[row]:[]};
  tables=[head,rows];
  if(options.ambiguous)tables.push({...head});
  const container={parentElement:body,querySelectorAll:()=>tables};
  head.parentElement=container;
  rows.parentElement=options.separate?body:container;
 }
 global.document={body,querySelectorAll:()=>tables};
}
setup();assert.equal(readPortalStatus(packet).state,'draft');
setup('Проект',{button:true});assert.equal(readPortalStatus(packet).url,'https://zakup.gov.kz/cabinet/purchase/my-applications/123');
setup('Подана');assert.equal(readPortalStatus(packet).state,'submitted');
setup('На рассмотрении');assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{otherLot:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{duplicate:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{wrongBin:true});assert.ok(readPortalStatus(packet).error);
setup('Проект',{split:true,button:true});assert.equal(readPortalStatus(packet).state,'draft');
setup('Подана',{split:true});assert.equal(readPortalStatus(packet).state,'submitted');
setup('Проект',{split:true,ambiguous:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{split:true,separate:true});assert.equal(readPortalStatus(packet).state,'unknown');
global.location.href='https://evil.example/cabinet/applications';assert.ok(readPortalStatus(packet).error);
console.log('Status reader: 12 cases passed');

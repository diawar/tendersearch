const assert=require('node:assert/strict');
const {readPortalStatus,statusTarget,reportTransferUrl}=require('./status.js');
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
 const header={innerText:'БИН: '+(options.wrongBin?'000000000000':packet.company_bin),getClientRects:()=>[1]};
 if(options.repeatedHeader)header.innerText+=' БИН: '+packet.company_bin;
 if(options.conflictingHeader)header.innerText+=' БИН: 111111111111';
 const body={innerText:header.innerText+(options.customerBin?' Заказчик БИН: 111111111111':'')};
 if(options.split){
  const head={getClientRects:()=>[1],querySelectorAll:s=>s==='thead th'?headers:[]};
  const rows={getClientRects:()=>[1],querySelectorAll:s=>s==='tbody tr'?[row]:[]};
  tables=[head,rows];
  if(options.ambiguous)tables.push({...head});
  const container={parentElement:body,querySelectorAll:()=>tables};
  head.parentElement=container;
  rows.parentElement=options.separate?body:container;
 }
 global.document={body,querySelectorAll:s=>s==='header,[role=banner],.ant-layout-header'?(options.noHeader?[]:[header]):tables};
}
setup();assert.equal(readPortalStatus(packet).state,'draft');
setup('Проект',{button:true});assert.equal(readPortalStatus(packet).url,'https://zakup.gov.kz/cabinet/purchase/my-applications/123');
setup('Подана');assert.equal(readPortalStatus(packet).state,'submitted');
setup('На рассмотрении');assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{otherLot:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{duplicate:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{wrongBin:true});assert.ok(readPortalStatus(packet).error);
setup('Проект',{customerBin:true,repeatedHeader:true});assert.equal(readPortalStatus(packet).state,'draft');
setup('Проект',{customerBin:true,wrongBin:true});assert.ok(readPortalStatus(packet).error);
setup('Проект',{conflictingHeader:true});assert.ok(readPortalStatus(packet).error);
setup('Проект',{noHeader:true});assert.ok(readPortalStatus(packet).error);
setup('Проект',{split:true,button:true});assert.equal(readPortalStatus(packet).state,'draft');
setup('Подана',{split:true});assert.equal(readPortalStatus(packet).state,'submitted');
setup('Проект',{split:true,ambiguous:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Проект',{split:true,separate:true});assert.equal(readPortalStatus(packet).state,'unknown');
setup('Завершена',{split:true,button:true});assert.equal(readPortalStatus(packet).state,'completed');
setup('Завершено');assert.equal(readPortalStatus(packet).state,'unknown'); // Announcement status is different.
setup('Завершена',{otherLot:true});assert.equal(readPortalStatus(packet).state,'unknown');
assert.deepEqual(statusTarget(packet),packet);
assert.deepEqual(statusTarget({...packet,lot_number:' 88524385–ЗЦП1 '}),packet);
for(const key of ['company_bin','announcement_number','lot_number'])assert.throws(()=>statusTarget({...packet,[key]:''}));
const report={schema:'goszakup-status-v1',...packet,state:'unknown'};
assert.ok(reportTransferUrl('http://localhost:8000',report).startsWith('http://localhost:8000/settings/extension/report/#'));
assert.ok(reportTransferUrl('https://example.test',report).startsWith('https://example.test/settings/extension/report/#'));
for(const address of ['http://external.test','javascript:alert(1)','https://user:pass@example.test','https://example.test/other','https://example.test/?x=1'])assert.throws(()=>reportTransferUrl(address,report));
global.location.href='https://evil.example/cabinet/applications';assert.ok(readPortalStatus(packet).error);
console.log('Status reader and manual target validation passed');

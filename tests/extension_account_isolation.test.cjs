const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('extensions/goszakup-assistant/background.js','utf8');
const id='abcdefghijklmnopabcdefghijklmnop',origin='http://localhost:8000';
const A={authenticated:true,scope:'a'.repeat(40),account:'1'.repeat(40),origin};
const B={authenticated:true,scope:'b'.repeat(40),account:'2'.repeat(40),origin};
let current=A,handler,executions=0;const session={},local={};
const storage=data=>({get:async key=>({[key]:structuredClone(data[key])}),set:async value=>Object.assign(data,structuredClone(value)),remove:async key=>{delete data[key];}});
const context=vm.createContext({URL,console,setTimeout,clearTimeout,importScripts:()=>{},GOSZAKUP_APP_ORIGINS:[origin],validateDraftPacket:()=>{},
 chrome:{runtime:{id,getURL:p=>'chrome-extension://'+id+'/'+p,onMessage:{addListener:fn=>handler=fn}},storage:{session:storage(session),local:storage(local)},
 alarms:{clear:async()=>{},create:async()=>{},onAlarm:{addListener:()=>{}}},action:{setBadgeText:async()=>{}},
 tabs:{sendMessage:async()=>structuredClone(current)},scripting:{executeScript:async()=>{executions++;return [];}}}});
vm.runInContext(source,context);
const sender={id,frameId:0,tab:{id:10},url:origin+'/workspace/drafts/'};
const popup={id,url:'chrome-extension://'+id+'/popup.html'};
const send=(message,from=sender)=>new Promise(resolve=>{if(!handler(message,from,resolve))resolve(null);});
const job=()=>({accountScope:A.scope,appOrigin:origin,status:'ready',number:'12345',packet:{company_bin:'241040001415',announcement_number:'17920228-1',lot_number:'88537093-ЗЦП1',data:{product_name:'PRIVATE A'},app_account:A.account},queue_context:{id:1}});
(async()=>{
 await send({type:'bridge:account',account:A});session.draftJob=job();
 assert.equal((await send({type:'draft:get'},popup)).job.packet.data.product_name,'PRIVATE A');
 current=B;assert.equal((await send({type:'draft:get'},popup)).job,null);assert.equal(session.draftJob,undefined);
 assert.equal((await send({type:'bridge:queue-status',account:B})).snapshot,null);
 assert.match((await send({type:'bridge:prepare',account:A,packet:job().packet})).error,/Учётная запись изменилась/);
 assert.equal(session.draftJob,undefined);
 session.draftJob={packet:{data:{product_name:'LEGACY SECRET'}}};assert.equal((await send({type:'draft:get'},popup)).job,null);
 session.draftJob={...job(),accountScope:B.scope};current={error:true};assert.ok((await send({type:'draft:get'},popup)).error);assert.equal(executions,0);
 await assert.rejects(vm.runInContext('writeJob('+JSON.stringify(job())+')',context),/Учётная запись изменилась/);
 const packet=job().packet,other={...packet,app_account:B.account};
 const keyA=vm.runInContext('recoveryKey('+JSON.stringify(packet)+')',context),keyB=vm.runInContext('recoveryKey('+JSON.stringify(other)+')',context);
 assert.notEqual(keyA,keyB);local[keyA]={number:'12345',createUncertain:true};
 assert.equal((await vm.runInContext('recovery('+JSON.stringify(other)+')',context)).number,'');assert.equal(local[keyA].number,'12345');
 // A late verification must not restore A's binding or return A's packet after B bound.
 current=A;await send({type:'bridge:account',account:A});session.draftJob=job();
 let release;const delayed=new Promise(resolve=>release=resolve);let first=true;
 context.chrome.tabs.sendMessage=async()=>{if(first){first=false;await delayed;return structuredClone(A);}return structuredClone(current);};
 const oldReply=send({type:'draft:get'},popup);await new Promise(resolve=>setImmediate(resolve));
 current=B;await send({type:'bridge:account',account:B});release();
 const late=await oldReply;assert.ok(!late.job);assert.equal(session.draftJob,undefined);assert.equal(session.appAccounts[origin].scope,B.scope);
 console.log('PASS: A→B popup/queue isolation, stale page rejected, legacy discarded, offline blocked, late writes blocked, scoped recovery');
})().catch(e=>{console.error(e);process.exitCode=1;});

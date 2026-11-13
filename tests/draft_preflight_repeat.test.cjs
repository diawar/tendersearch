const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const{draftPreflightDecision}=require('../extensions/goszakup-assistant/check.js');
const packet={company_bin:'241040001415',announcement_number:'17932745-1',announcement_external_id:'17932745',lot_number:'87944589-ЗЦП3',app_account:'1'.repeat(40)};
async function scenario(initial,crashAfterCreate=false){
 let portal=initial,creates=0,checks=0;const session={draftJob:{packet:{...packet},appOrigin:'http://localhost:8000',accountScope:'a'.repeat(40),tabId:1,status:'running'},appAccounts:{'http://localhost:8000':{scope:'a'.repeat(40)}}},local={};
 const storage=data=>({get:async k=>({[k]:data[k]}),set:async v=>Object.assign(data,v),remove:async k=>{delete data[k];}});
 const context=vm.createContext({URL,Date,Map,setTimeout,clearTimeout,draftPreflightDecision,checkSavedApplication:()=>{},runDraftStep:()=>{},importScripts:()=>{},
 chrome:{runtime:{onMessage:{addListener:()=>{}}},storage:{session:storage(session),local:storage(local)},alarms:{clear:async()=>{},create:async()=>{},onAlarm:{addListener:()=>{}}},
 tabs:{create:async()=>({id:2}),get:async()=>({status:'complete'}),update:async()=>{},remove:async()=>{}},
 scripting:{executeScript:async arg=>{
  if(arg.func===context.checkSavedApplication){checks++;return [{result:{...packet,observed_at:new Date().toISOString(),check_state:portal==='absent'?'absent':portal==='unknown'?'unknown':'found',found_number:portal==='absent'?'':portal,application_state:'draft'}}];}
  const action=arg.args[1],number=arg.args[2].applicationNumber;
  if(action==='probe')return [{result:number?{state:'complete',number,lotConfirmed:true,message:'Готов'}:{state:'account',next:'save_account',message:'Реквизиты'}}];
  if(action==='save_account'){creates++;portal='999';if(crashAfterCreate){crashAfterCreate=false;throw Error('Frame was removed');}return [{result:{state:'draft_created',number:portal,lotConfirmed:true,message:'Создан'}}];}
  throw Error('Unexpected portal action '+action);
 }}}});
 vm.runInContext(fs.readFileSync('extensions/goszakup-assistant/background.js','utf8'),context);vm.runInContext('verifyBoundJob=async()=>true',context);
 await vm.runInContext('runJob()',context);await vm.runInContext('runJob()',context);
 return {creates,checks,job:session.draftJob};
}
(async()=>{
 const existing=await scenario('123');assert.equal(existing.creates,0);assert.equal(existing.checks,2);assert.equal(existing.job.number,'123');
 const fresh=await scenario('absent');assert.equal(fresh.creates,1);assert.equal(fresh.checks,2);assert.equal(fresh.job.number,'999');
 const unknown=await scenario('unknown');assert.equal(unknown.creates,0);assert.equal(unknown.job.status,'blocked');
 const recovered=await scenario('absent',true);assert.equal(recovered.creates,1);assert.equal(recovered.job.status,'complete');assert.equal(recovered.job.number,'999');
 console.log('PASS: two complete launches reuse existing draft (0 creates); absent then retry creates exactly once; unknown never creates');
})().catch(e=>{console.error(e);process.exitCode=1;});

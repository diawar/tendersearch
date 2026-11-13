const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {draftPreflightDecision}=require('../extensions/goszakup-assistant/check.js');
const packet={company_bin:'241040001415',announcement_number:'17932745-1',announcement_external_id:'17932745',lot_number:'87944589-ЗЦП3',app_account:'1'.repeat(40)};
const result=(state,extra={})=>({...packet,observed_at:new Date().toISOString(),check_state:state,...extra});
const job=()=>({packet:{...packet},appOrigin:'http://localhost:8000',accountScope:'a'.repeat(40),tabId:1,status:'running',number:'123'});
assert.equal(draftPreflightDecision(job(),result('found',{found_number:'456',application_state:'draft'})).action,'resume');
for(const state of ['submitted','completed','withdrawn',undefined])assert.equal(draftPreflightDecision(job(),result('found',{found_number:'123',application_state:state})).action,'block');
assert.equal(draftPreflightDecision(job(),result('absent')).action,'create');
assert.equal(draftPreflightDecision({...job(),createUncertain:true},result('absent')).action,'wait');
assert.equal(draftPreflightDecision({...job(),createUncertain:true,absenceConfirmedAt:Date.now()-61000},result('absent')).action,'create');
assert.equal(draftPreflightDecision(job(),result('unknown')).action,'block');
assert.equal(draftPreflightDecision(job(),result('absent',{company_bin:'000000000000'})).action,'block');
assert.equal(draftPreflightDecision(job(),result('absent',{observed_at:'2020-01-01T00:00:00Z'})).action,'block');
async function scenario(check,uncertain=false){
 const state={draftJob:{...job(),createUncertain:uncertain},appAccounts:{'http://localhost:8000':{scope:'a'.repeat(40)}}},local={};let navigated=0,closed=0;
 const storage=data=>({get:async key=>({[key]:data[key]}),set:async value=>Object.assign(data,value),remove:async key=>{delete data[key];}});
 const context=vm.createContext({URL,Date,Map,setTimeout,clearTimeout,draftPreflightDecision,checkSavedApplication:()=>{},importScripts:()=>{},
 chrome:{runtime:{onMessage:{addListener:()=>{}}},storage:{session:storage(state),local:storage(local)},alarms:{clear:async()=>{},create:async()=>{},onAlarm:{addListener:()=>{}}},
 tabs:{create:async()=>({id:2}),get:async()=>({status:'complete'}),update:async()=>{navigated++;},remove:async()=>{closed++;}},scripting:{executeScript:async()=>[{result:check}]}}});
 vm.runInContext(fs.readFileSync('extensions/goszakup-assistant/background.js','utf8'),context);
 vm.runInContext('verifyBoundJob=async()=>true',context);
 const allowed=await vm.runInContext('preflightDraft()',context);
 return {allowed,stored:state.draftJob,navigated,closed};
}
(async()=>{
 const existing=await scenario(result('found',{found_number:'456',application_state:'draft'}));assert.equal(existing.allowed,true);assert.equal(existing.stored.number,'456');assert.equal(existing.stored.packet.existing_application_number,'456');assert.equal(existing.closed,1);
 const absent=await scenario(result('absent'));assert.equal(absent.allowed,true);assert.equal(absent.stored.number,'');assert.equal(absent.stored.packet.existing_application_number,'');
 for(const check of [result('unknown'),result('found',{found_number:'123',application_state:'submitted'}),null]){const blocked=await scenario(check);assert.equal(blocked.allowed,false);assert.equal(blocked.navigated,0);assert.equal(blocked.stored.status,'blocked');assert.equal(blocked.closed,1);}
 const waiting=await scenario(result('absent'),true);assert.equal(waiting.allowed,false);assert.equal(waiting.stored.status,'waiting');assert.ok(waiting.stored.absenceConfirmedAt);assert.equal(waiting.navigated,0);
 const outage=await scenario(result('unknown',{retryable:true}),true);assert.equal(outage.allowed,false);assert.equal(outage.stored.status,'waiting');assert.equal(outage.stored.absenceConfirmedAt,null);assert.equal(outage.navigated,0);
 console.log('PASS: existing draft resumed, explicit absence allowed, unknown/submitted blocked, uncertain creation requires second check, verification tabs closed');
})().catch(e=>{console.error(e);process.exitCode=1;});

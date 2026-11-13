const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const {validateDraftPacket}=require('./draft.js');
const packet={schema:'goszakup-preparation-v1',company_bin:'241040001415',announcement_external_id:'17920228',
 announcement_number:'17920228-1',lot_number:'88537093-ЗЦП1',purchase_method_id:12,single_position:true,
 data:{product_name:'ТЕСТ комплект',manufacturer:'ТЕСТ изготовитель',country:'Китай',legal_address:'Тестовая 1',bank_account:'KZ000000000000000000',contact_phone:'+77000000000'}};
validateDraftPacket(packet);
validateDraftPacket({...packet,position_count:2});
validateDraftPacket({...packet,position_count:2,repeat_product:true});
for(const bad of [{purchase_method_id:11},{single_position:false},{announcement_external_id:'99'},
 {company_bin:'invalid'},{data:{...packet.data,manufacturer:''}},{data:{...packet.data,bank_account:'bad'}}])assert.throws(()=>validateDraftPacket({...packet,...bad}));

// The former draft:load command was deliberately removed for account isolation.
// Exercise the current authenticated bridge and preflight/recovery flows instead
// of seeding legacy unbound jobs that production correctly refuses to execute.
for(const file of ['extension_account_isolation.test.cjs','draft_preflight.test.cjs','draft_preflight_repeat.test.cjs']){
 const run=spawnSync(process.execPath,[path.join(__dirname,'../../tests',file)],{cwd:path.join(__dirname,'../..'),encoding:'utf8'});
 process.stdout.write(run.stdout||'');process.stderr.write(run.stderr||'');
 assert.equal(run.status,0,file);
}
console.log('Draft packet validation and current account-bound orchestration passed');
module.exports={packet};

const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},pending=[];let changed;
const node=id=>nodes[id]??=( {value:'',textContent:'',dataset:{},addEventListener:()=>{}} );
let statusResult;const statusPromise=new Promise(resolve=>statusResult=resolve);
vm.runInNewContext(fs.readFileSync('extensions/goszakup-assistant/popup.js','utf8'),{
 URL,statusTarget:value=>value,readPortalStatus:()=>{},document:{querySelector:node},chrome:{runtime:{sendMessage:()=>new Promise(resolve=>pending.push(resolve))},
 tabs:{query:async()=>[{id:1,url:'https://zakup.gov.kz/cabinet/purchase/my-applications'}]},scripting:{executeScript:()=>statusPromise},
 storage:{onChanged:{addListener:fn=>changed=fn}}}});
(async()=>{
 assert.equal(pending.length,1);
 changed({appAccounts:{}},'session');assert.equal(pending.length,2);
 pending[1]({job:null});await new Promise(resolve=>setImmediate(resolve));
 pending[0]({job:{accountScope:'old-a',packet:{company_bin:'PRIVATE A',lot_number:'PRIVATE LOT'},status:'ready',message:'PRIVATE A'}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(node('#company_bin').value,'');assert.equal(node('#lot_number').value,'');
 assert.equal(node('#draft-state').textContent,'Подготовка не загружена.');assert.equal(node('#result-summary').textContent,'');
 const check=node('#status').onclick();await new Promise(resolve=>setImmediate(resolve));
 changed({appAccounts:{}},'session');pending[2]({job:null});await new Promise(resolve=>setImmediate(resolve));
 statusResult([{result:{state:'draft',number:'PRIVATE A APPLICATION'}}]);await check;
 assert.equal(node('#send').disabled,true);assert.equal(node('#result-summary').textContent,'');
 console.log('PASS: late popup response cannot redisplay prior account preparation');
})().catch(e=>{console.error(e);process.exitCode=1;});

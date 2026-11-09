const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const nodes={};
for(const id of ['transfer-result','transfer-retry','transfer-links','transfer-lot','transfer-preparation']){
 nodes['#'+id]={hidden:true,disabled:false,listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},remove(){this.removed=true;}};
}
nodes['#transfer-form [name=csrfmiddlewaretoken]']={value:'test-csrf'};
let calls=0;
const context={document:{querySelector:key=>nodes[key]},location:{hash:'#'+encodeURIComponent(JSON.stringify({schema:'goszakup-status-v1',state:'completed',number:'73727651'})),pathname:'/settings/extension/report/',search:''},history:{replaceState(){}},
 fetch:async()=>{calls++;return {ok:true,redirected:false,json:async()=>({saved:true,state:'completed',number:'73727651',lot_url:'/lots/1/',preparation_url:'/lots/1/preparation/'})};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/tender_search/core/static/core/extension_report.js'),'utf8'),context);
(async()=>{
 const button=nodes['#transfer-retry'];
 assert.equal(calls,0); // No write before the user confirms the account.
 const first=button.listeners.click();
 await button.listeners.click();
 await first;
 assert.equal(calls,1);
 assert.equal(button.removed,true);
 assert.equal(nodes['#transfer-links'].hidden,false);
 assert.match(nodes['#transfer-result'].textContent,/Отчёт сохранён/);
 await button.listeners.click();
 assert.equal(calls,1); // A stale handler cannot send a null report after success.
 console.log('Transfer confirmation and duplicate-click regression passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

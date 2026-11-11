const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('src/tender_search/core/static/core/lot-detail.js','utf8');
let requests=0,sequence=0;const timers=new Map(),events={};
const root={dataset:{pending:'true',detail:'a',documents:'b',specification:'c',statusUrl:'/lots/1/status/'},append(){},addEventListener(){}};
const document={hidden:false,body:{dataset:{userId:'7'}},getElementById:id=>id==='lot-detail'?root:null,
 createElement:()=>({setAttribute(){}}),addEventListener:(name,fn)=>events[name]=fn};
vm.runInNewContext(code,{document,window:{},URL,location:{origin:'http://localhost:8000'},
 AbortSignal:{timeout:()=>({})},setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id),
 fetch:async()=>{requests++;return {ok:true,redirected:false,json:async()=>({owner:7,pending:false,revisions:{detail:'a',documents:'b',specification:'c'}})};}});
(async()=>{
 assert.equal(timers.size,1);
 const queued=[...timers.values()][0];timers.clear();
 document.hidden=true;events.visibilitychange();await queued();
 assert.equal(requests,0,'a callback queued before hiding must not poll');
 document.hidden=false;events.visibilitychange();
 for(const fn of [...timers.values()]){timers.clear();await fn();}
 assert.equal(requests,1,'resume the pending card on return');
 assert.equal(timers.size,0,'stop when background work is complete');
 console.log('Lot card polling: hidden callback, foreground resume and completion stop passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('src/tender_search/core/static/core/background-poll.js','utf8');
function tab(hidden=false,install=true){
 const handlers={},windowEvents={},resumed=[];
 const element={hasAttribute:name=>name==='data-background-poll'};
 const document={hidden,querySelectorAll:()=>[element],addEventListener:(name,fn)=>handlers[name]=fn};
 const window={htmx:{trigger:(node,event)=>resumed.push({node,event})},addEventListener:(name,fn)=>windowEvents[name]=fn};
 if(install)vm.runInNewContext(code,{document,window,WeakSet});
 function request(node=element){
  const event={detail:{elt:node},preventDefault(){this.cancelled=true;}};
  handlers['htmx:confirm']?.(event);
  if(event.cancelled)return false;
  handlers['htmx:configRequest']?.(event);
  if(node===element&&install)assert.equal(event.detail.timeout,20000);
  if(node!==element)assert.equal(event.detail.timeout,undefined,'do not change timeouts of manual mutations');
  handlers['htmx:beforeRequest']?.(event);
  if(event.cancelled)return false;
  handlers['htmx:beforeSend']?.(event);return true;
 }
 const finish=()=>handlers['htmx:afterRequest']?.({detail:{elt:element}});
 return {document,handlers,windowEvents,element,resumed,request,finish};
}
const visible=tab();assert.equal(visible.request(),true);
assert.equal(visible.request({hasAttribute:()=>false}),true,'active polling never drops a nested manual mutation');
assert.equal(visible.request(),false,'do not queue another automatic GET while the first runs');
visible.finish();assert.equal(visible.request(),true);visible.finish();
visible.document.hidden=true;assert.equal(visible.request(),false);
assert.equal(visible.request({hasAttribute:()=>false}),true,'manual actions are not background polling');
visible.handlers.visibilitychange();assert.equal(visible.resumed.length,0);
visible.document.hidden=false;visible.handlers.visibilitychange();
assert.equal(visible.resumed.length,1);assert.equal(visible.resumed[0].event,'app:resume');
visible.windowEvents.pageshow({persisted:false});assert.equal(visible.resumed.length,1);
visible.windowEvents.pageshow({persisted:true});assert.equal(visible.resumed.length,2);
visible.request();visible.handlers.visibilitychange();assert.equal(visible.resumed.length,2,'wait for the existing request');
// Fixed 60-second workload: five workspace tabs, only one visible, 30s refresh.
function minute(install){
 const tabs=Array.from({length:5},(_,i)=>tab(i!==0,install));let requests=0;
 for(const second of [30,60])for(const page of tabs){if(page.request())requests++;page.finish();}
 return requests;
}
assert.equal(minute(false),10);assert.equal(minute(true),2);
console.log('Background polling: hidden tabs, in-flight duplicates, resume and manual actions passed; five-tab workspace workload: 10 -> 2 requests/minute');

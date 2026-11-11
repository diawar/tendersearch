const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const full=fs.readFileSync('src/tender_search/core/static/core/search.js','utf8');
const code=full.slice(full.indexOf('// Refresh only personal controls'),full.indexOf('document.addEventListener("change",event=>{if(event.target.matches("select[data-page-size]"))'));
let now=100000,auto=true,requests=0,interval,channel,waiting;
const handlers={},windows={},timers=new Map();let sequence=0;
const action={dataset:{lotId:'1'},querySelector:()=>null};
const counters={'#sidebar-collection-count':{textContent:''},'#sidebar-favorite-count':{textContent:''}};
const document={hidden:false,body:{dataset:{userId:'7',lotStateUrl:'/lots/ui-state/'}},
 querySelector:selector=>selector.startsWith('#collection-workspace')&&auto?{}:counters[selector]||null,
 querySelectorAll:selector=>selector==='.lot-actions[data-lot-id]'?[action]:[],
 addEventListener:(name,fn)=>handlers[name]=fn};
const context={document,window:{addEventListener:(name,fn)=>windows[name]=fn},Date:{now:()=>now},
 setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id),
 setInterval:fn=>interval=fn,AbortSignal:{timeout:()=>({})},
 BroadcastChannel:class{constructor(){channel=this;}postMessage(){}},
 fetch:async()=>{requests++;if(waiting)await waiting;return {ok:true,json:async()=>({owner:'7',lots:[],favorite_count:0})};}};
vm.runInNewContext(code,context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function flush(){const pending=[...timers.values()];timers.clear();for(const fn of pending)fn();await settle();}
(async()=>{
 windows.pageshow({persisted:false});windows.focus();await flush();assert.equal(requests,0,'fresh server HTML needs no immediate poll');
 now+=30000;interval();await flush();assert.equal(requests,0,'workspace HTML refresh already contains the same controls');
 handlers['htmx:afterSwap']({detail:{elt:{id:'collection-workspace',dataset:{collectionCount:'123',favoriteCount:'8'}}}});
 assert.equal(counters['#sidebar-collection-count'].textContent,'(123)');
 assert.equal(counters['#sidebar-favorite-count'].textContent,'(8)');
 handlers['htmx:afterRequest']({detail:{successful:true,requestConfig:{path:'/lots/1/favorite/'}}});await flush();assert.equal(requests,1);
 document.hidden=true;channel.onmessage();await flush();assert.equal(requests,1);
 document.hidden=false;handlers.visibilitychange();await flush();assert.equal(requests,2,'hidden mutation is checked on return');
 auto=false;now+=30000;interval();await flush();assert.equal(requests,3,'ordinary search/favorites still refresh controls');
 let release;waiting=new Promise(resolve=>release=resolve);now+=30000;interval();await flush();assert.equal(requests,4);
 channel.onmessage();await flush();assert.equal(requests,4,'coalesce mutations while a request runs');
 auto=true;release();waiting=null;await settle();await flush();assert.equal(requests,5,'forced mutation check survives a concurrent refresh');
 handlers['htmx:beforeSend']({detail:{elt:{id:'collection-workspace'}}});
 channel.onmessage();await flush();assert.equal(requests,6);
 handlers['htmx:afterSwap']({detail:{elt:{id:'collection-workspace',dataset:{collectionCount:'122',favoriteCount:'7'}}}});
 await flush();assert.equal(requests,7,'recheck when a table response predates a cross-tab mutation');
 handlers['htmx:afterRequest']({detail:{elt:{id:'collection-workspace'},requestConfig:{path:'/workspace/'}}});
 console.log('Personal controls: no initial/HTML-refresh duplicate, visible updates, hidden mutations and in-flight coalescing passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

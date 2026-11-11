const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const code=fs.readFileSync('src/tender_search/core/static/core/deadline.js','utf8');
let tick=0,interval,intervals=0;
const events={};
function element(end,now){
  return {dataset:{deadline:end,deadlineNow:now},textContent:'fallback',urgent:false,
    classList:{toggle(name,on){this.owner.urgent=on;}}};
}
const now='2026-11-11T12:00:00+05:00';
const long=element('2026-11-13T15:04:05+05:00',now);
const short=element('2026-11-11T07:00:02Z',now);
const invalid=element('invalid',now);
let nodes=[long,short,invalid];
function prepare(node){node.classList.owner=node;return node;}
nodes.forEach(prepare);
const document={hidden:false,querySelectorAll:()=>nodes,addEventListener:(event,fn)=>events[event]=fn};
vm.runInNewContext(code,{document,Date,performance:{now:()=>tick},WeakMap,
  setInterval:(fn,ms)=>{assert.equal(ms,1000);interval=fn;intervals++;}});
assert.equal(intervals,1);
assert.equal(long.textContent,'Осталось: 2 д 03:04:05');
assert.equal(long.urgent,false);
assert.equal(short.textContent,'Осталось: 00:00:02');
assert.equal(short.urgent,true);
assert.equal(invalid.textContent,'fallback');
tick=1500;interval();
assert.equal(short.textContent,'Осталось: 00:00:01');
tick=2000;interval();
assert.equal(short.textContent,'Приём завершён');
assert.equal(short.urgent,false);
// A fragment replacement starts at its own fresh server timestamp.
const replacement=prepare(element('2026-11-11T12:00:10+05:00','2026-11-11T12:00:02+05:00'));
nodes=[replacement];events['htmx:afterSwap']();
assert.equal(replacement.textContent,'Осталось: 00:00:08');
document.hidden=true;tick+=4000;interval();
assert.equal(replacement.textContent,'Осталось: 00:00:08');
document.hidden=false;events.visibilitychange();
assert.equal(replacement.textContent,'Осталось: 00:00:04');
nodes=[];interval();
console.log('Deadline countdown: server clock, timezones, seconds, expiry, HTMX and hidden tabs passed');

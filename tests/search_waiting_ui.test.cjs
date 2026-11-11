const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const full=fs.readFileSync('src/tender_search/core/static/core/search.js','utf8');
const code=full.slice(full.indexOf('// HTMX replaces pending results'));
let now=100000,art,interval;
const handlers={};
const reduced={matches:false};
function makeArt(){
 const frames=Array.from({length:3},()=>({hidden:false}));
 const dots=frames.map(()=>({active:false,classList:{toggle(name,value){this.owner.active=value;}}}));
 dots.forEach(dot=>dot.classList.owner=dot);
 return {dataset:{searchStart:'100'},frames,dots,querySelectorAll(selector){return selector==='[data-progress-frame]'?frames:dots;}};
}
art=makeArt();
vm.runInNewContext(code,{window:{matchMedia:()=>reduced},Date:{now:()=>now},
 document:{querySelectorAll:()=>art?[art]:[],addEventListener:(name,handler)=>handlers[name]=handler},
 setInterval:handler=>interval=handler});
assert.deepEqual(art.frames.map(frame=>frame.hidden),[false,true,true]);
now+=4500;interval();
assert.deepEqual(art.frames.map(frame=>frame.hidden),[true,false,true]);
art=makeArt();handlers['htmx:afterSwap']();
assert.deepEqual(art.frames.map(frame=>frame.hidden),[true,false,true],'polling must not restart the sequence');
now+=4000;interval();
assert.deepEqual(art.frames.map(frame=>frame.hidden),[true,true,false]);
reduced.matches=true;interval();
assert.deepEqual(art.frames.map(frame=>frame.hidden),[false,true,true]);
art=null;interval();
console.log('Waiting illustrations: rotation, HTMX continuity, completion and reduced motion passed');

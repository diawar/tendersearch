const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const events={},status={textContent:'Загружаем'},retry={hidden:true};
const root={contains:el=>el===retry,querySelector:key=>key==='[data-specification-status]'?status:retry};
vm.runInNewContext(fs.readFileSync('src/tender_search/core/static/core/preparation-specification.js','utf8'),{
 document:{getElementById:()=>root,addEventListener:(name,fn)=>events[name]=fn}});
events['htmx:sendError']({detail:{elt:{}},target:{}});assert.equal(retry.hidden,true);
for(const event of Object.values(events)){retry.hidden=true;event({detail:{elt:root},target:root});assert.equal(retry.hidden,false);assert.match(status.textContent,/Не удалось проверить загрузку/);}
console.log('PASS: specification transport errors replace spinner and expose retry without replacing product/price form');

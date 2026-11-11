const assert=require('node:assert/strict');
const {parseMoney,calculate,checkBudget}=require('../src/tender_search/core/static/core/pricing.js');
const budget=value=>checkBudget(value,10000n,30000n);
for(const [rate,equal,over,discount,above] of [['16','116','116.01','104.40','104.41'],['0','100','100.01','90','90.01']]){
 const result=text=>calculate(parseMoney(text),rate,'3');
 assert.equal(budget(result(equal)).exceeds,false);
 assert.equal(budget(result(over)).exceeds,true);
 assert.equal(budget(result(discount)).discounted,true);
 assert.equal(budget(result(above)).discounted,false);
 assert.equal(result(equal).total_net,30000n);
}
assert.equal(checkBudget(calculate(parseMoney('116'),'16','3'),10000n,29999n).exceeds,true);
assert.equal(budget(null).discounted,false);
console.log('VAT/non-VAT budget and 10% threshold checks passed');

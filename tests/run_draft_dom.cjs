const cp=require('node:child_process'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
require('./draft_dom_fixture.cjs');
const chrome=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const result=cp.spawnSync(chrome,['--headless=new','--disable-gpu','--disable-background-networking','--no-first-run',
 '--user-data-dir='+path.resolve('data/draft-fixture-profile3'),'--dump-dom','--virtual-time-budget=40000',
 pathToFileURL(path.resolve('data/draft-dom-fixture.html')).href],{encoding:'utf8',timeout:60000});
fs.writeFileSync('data/draft-dom-result3.html',result.stdout||'');
const message=(result.stdout||'').match(/<pre id="output">([^<]*)/)?.[1];
console.log(message||result.error?.message||'No browser test result');
if(!message?.startsWith('PASS:'))process.exitCode=1;

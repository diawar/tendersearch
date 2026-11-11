const fs=require('fs'),path=require('path'),http=require('http'),cp=require('child_process');
(async()=>{
 const deadline=setTimeout(()=>{console.error('Browser probe timeout');process.exit(1)},25000);
 const server=http.createServer((q,r)=>{r.setHeader('Content-Type','text/html; charset=utf-8');r.end('<html><body><div id="queue-message">OLD CONNECTION ERROR</div><form id="queue-csrf"><input name="csrfmiddlewaretoken" value="test"></form><div data-queue-id="1" data-state="queued" data-generation="11111111-1111-4111-8111-111111111111"><span class="queue-state"></span><span class="queue-detail"></span></div><script>'+fs.readFileSync('src/tender_search/core/static/core/draft_queue.js','utf8')+'</script></body></html>');});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 const ext=path.resolve('data/bridge-browser-extension');fs.mkdirSync(ext,{recursive:true});
 for(const f of fs.readdirSync('extensions/goszakup-assistant'))if(!f.endsWith('.cjs'))fs.copyFileSync(path.join('extensions/goszakup-assistant',f),path.join(ext,f));
 fs.writeFileSync(path.join(ext,'bridge-config.js'),'globalThis.GOSZAKUP_APP_ORIGINS='+JSON.stringify([origin])+';');
 const browser=cp.spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--no-first-run','--no-proxy-server','--enable-unsafe-extension-debugging','--disable-gpu','--disable-background-networking','--disable-features=DisableLoadExtensionCommandLineSwitch','--load-extension='+ext,'--user-data-dir='+path.resolve('data/bridge-browser-profile-'+Date.now()),'--remote-debugging-port=0','about:blank'],{stdio:['ignore','ignore','pipe']});
 let socket;
 try{
 const endpoint=await new Promise((resolve,reject)=>{let s='';const timer=setTimeout(()=>reject(Error('No CDP')),15000);browser.stderr.on('data',b=>{s+=b;if(s.match(/DevTools listening on (ws:\/\/[^\s]+)/)){clearTimeout(timer);resolve(s.match(/DevTools listening on (ws:\/\/[^\s]+)/)[1]);}});});
 socket=new WebSocket(endpoint);await new Promise(r=>socket.addEventListener('open',r));let seq=0;const pending=new Map();socket.addEventListener('message',e=>{const d=JSON.parse(e.data);if(d.id){pending.get(d.id)?.(d);pending.delete(d.id);}});
 const rpc=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,d=>d.error?reject(Error(JSON.stringify(d.error))):resolve(d.result));socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});
 console.log('extension',await rpc('Extensions.loadUnpacked',{path:ext}));
 const {targetId}=await rpc('Target.createTarget',{url:origin+'/workspace/drafts/'});const {sessionId}=await rpc('Target.attachToTarget',{targetId,flatten:true});
 await new Promise(r=>setTimeout(r,2500));
 const result=await rpc('Runtime.evaluate',{expression:`new Promise(resolve=>{const log=[];window.addEventListener('message',e=>{log.push(e.data);if(e.data.type==='ready')resolve(log)});window.postMessage({channel:'goszakup-preparation-bridge-v1',id:'11111111-1111-4111-8111-111111111111',type:'ping'},location.origin);setTimeout(()=>resolve(log),2500)})`,awaitPromise:true,returnByValue:true},sessionId);if(!result.result.value?.some(value=>value.type==='ready'&&value.autoQueue===true))throw Error('No actual extension handshake');
 const ui=await rpc('Runtime.evaluate',{expression:"document.querySelector('#queue-message').textContent",returnByValue:true},sessionId);console.log('queue_message',JSON.stringify(ui));if(!ui.result.value.includes('Расширение подключено'))throw Error('Connection failed');
 await rpc('Browser.close');socket.close();
 }finally{socket?.close();browser.kill();server.close();clearTimeout(deadline);}
})().catch(e=>{console.error(e);process.exitCode=1;});

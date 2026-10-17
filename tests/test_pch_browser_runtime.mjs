import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {mkdtemp,mkdir,writeFile,readdir,rm,access} from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import {runBrowserJourney,testStorage} from './pch_browser_runtime.mjs';

const storage=process.env.PCH_TEST_STORAGE||path.resolve(import.meta.dirname,'../.venv/pch-test-workspaces');
const fixtureSource=`
const fs=require('node:fs'),net=require('node:net'),readline=require('node:readline');
fs.readFileSync(3,'utf8');
if(process.argv[2]==='startup-fail')process.exit(3);
fs.writeFileSync(process.env.TMPDIR+'/owned-state','small disposable fixture');
const server=net.createServer(socket=>socket.end('fixture'));
server.listen(0,'127.0.0.1',()=>console.log(JSON.stringify({ready:true,origin:'http://127.0.0.1:'+server.address().port})));
if(process.argv[2]==='stubborn')process.on('SIGTERM',()=>{});
readline.createInterface({input:process.stdin}).on('line',line=>{
 if(JSON.parse(line).action==='stop'&&process.argv[2]!=='stubborn')server.close(()=>process.exit(0));
});
`;
function fakeBrowser(){
  const page=new EventEmitter();
  page.setDefaultTimeout=page.setDefaultNavigationTimeout=()=>{};
  page.locator=()=>({innerText:async()=> 'minimal failure diagnosis'});
  let closed=0,disconnected=0;
  return {contexts:()=>[{clearCookies:async()=>{},pages:()=>[page]}],
    newBrowserCDPSession:async()=>({send:async method=>{assert.equal(method,'Browser.close');closed++;}}),
    close:async()=>{disconnected++;},counts:()=>({closed,disconnected})};
}
async function portClosed(origin){
  const port=Number(new URL(origin).port);
  return new Promise(resolve=>{
    const socket=net.connect({host:'127.0.0.1',port});
    socket.on('error',()=>resolve(true));socket.on('connect',()=>{socket.destroy();resolve(false);});
  });
}
for(const mode of ['success','failure','timeout','cancel','SIGINT','SIGTERM','startup-fail','stubborn']){
  test(`owned fixture lifecycle: ${mode}`,async()=>{
    await testStorage(storage);
    const root=await mkdtemp(path.join(storage,'runtime-regression-'));
    const work=path.join(root,'work'),evidenceRoot=path.join(root,'evidence');
    await mkdir(work);await mkdir(evidenceRoot);
    const script=path.join(root,'fixture.cjs');await writeFile(script,fixtureSource);
    const browser=fakeBrowser(),controller=new AbortController();
    let origin;
    const oldExit=process.exitCode;
    try{
      const evidence=await runBrowserJourney('lifecycle',async info=>{
        origin=info.origin;
        if(mode==='failure')throw new Error('representative journey failure');
        if(mode==='cancel'){controller.abort();await new Promise(()=>{});}
        if(mode==='SIGINT'||mode==='SIGTERM'){process.kill(process.pid,mode);await new Promise(()=>{});}
        if(mode==='timeout'||mode==='stubborn')await new Promise(()=>{});
      },{storageRoot:work,evidenceRoot,cdp:'http://127.0.0.1:19999',
        fixtureCommand:[process.execPath,script,mode],connectBrowser:async()=>browser,
        timeoutMs:mode==='timeout'||mode==='stubborn'?250:3000,cleanupTimeoutMs:100,signal:controller.signal});
      assert.equal(evidence.result,mode==='success'?'PASS':'FAIL');
      assert.equal(evidence.fixtureTerminal,true);
      assert.equal(evidence.workspaceRemoved,true);
      assert.deepEqual(await readdir(work),[]);
      if(evidence.fixtureIdentity){
        await assert.rejects(access('/proc/'+evidence.fixtureIdentity.pid));
        assert.equal(await portClosed(origin),true);
        assert.deepEqual(browser.counts(),{closed:1,disconnected:1});
      }
      if(mode==='stubborn'){
        assert.equal(evidence.fixtureTermination.signal,'SIGKILL');
        assert.equal(evidence.cleanupFailure,'fixture required termination');
      }
      if(mode==='startup-fail')assert.equal(evidence.fixtureExit,3);
      assert.ok((await readdir(evidenceRoot)).includes('lifecycle-browser.json'));
    }finally{process.exitCode=oldExit;await rm(root,{recursive:true});}
  });
}
test('tmpfs test storage is rejected before spawning',async()=>{
  // Linux /dev/shm is explicitly a tmpfs mount; no directory is created there.
  await assert.rejects(testStorage('/dev/shm'),/tmpfs or ramfs/);
});

test('existing Python fixture serves and releases its actual socket and disk workspace',async()=>{
  await testStorage(storage);
  const root=await mkdtemp(path.join(storage,'runtime-real-fixture-'));
  const work=path.join(root,'work');await mkdir(work);
  let origin;
  const oldExit=process.exitCode;
  try{
    const evidence=await runBrowserJourney('existing-fixture',async info=>{
      origin=info.origin;
      const response=await fetch(origin+'/api/health');
      assert.equal(response.status,200);
    },{storageRoot:work,evidenceRoot:path.join(root,'evidence'),cdp:'http://127.0.0.1:19999',
      connectBrowser:async()=>fakeBrowser(),timeoutMs:15000,cleanupTimeoutMs:5000});
    assert.equal(evidence.result,'PASS');assert.equal(evidence.fixtureTerminal,true);
    assert.equal(evidence.workspaceRemoved,true);assert.deepEqual(await readdir(work),[]);
    await assert.rejects(access('/proc/'+evidence.fixtureIdentity.pid));
    assert.equal(await portClosed(origin),true);
  }finally{process.exitCode=oldExit;await rm(root,{recursive:true});}
});

for(const mode of ['abort','fixture-SIGTERM'])test(`existing fixture releases held batch on ${mode}`,async()=>{
  await testStorage(storage);
  const root=await mkdtemp(path.join(storage,'runtime-batch-'));
  const work=path.join(root,'work');await mkdir(work);
  const controller=new AbortController();let origin,batch;
  const oldExit=process.exitCode;
  try{
    const evidence=await runBrowserJourney('held-batch',async info=>{
      origin=info.origin;
      batch=await info.waitMessage(message=>message.batch_held,{action:'hold-batch'});
      if(mode==='fixture-SIGTERM'){
        const {readFile}=await import('node:fs/promises');
        const leader=JSON.parse(await readFile(path.join(root,'evidence','active-fixture.json'),'utf8'));
        const raw=await readFile('/proc/'+leader.pid+'/stat','utf8');
        assert.equal(raw.slice(raw.lastIndexOf(') ')+2).split(' ')[19],leader.start_ticks);
        process.kill(leader.pid,'SIGTERM');
      }
      controller.abort();await new Promise(()=>{});
    },{storageRoot:work,evidenceRoot:path.join(root,'evidence'),cdp:'http://127.0.0.1:19999',
      connectBrowser:async()=>fakeBrowser(),timeoutMs:15000,cleanupTimeoutMs:5000,signal:controller.signal});
    assert.equal(evidence.result,'FAIL');assert.equal(evidence.fixtureTerminal,true);
    assert.equal(evidence.fixtureGroupTerminal,true);assert.equal(evidence.workspaceRemoved,true);
    assert.deepEqual(await readdir(work),[]);assert.equal(await portClosed(origin),true);
    await assert.rejects(access('/proc/'+evidence.fixtureIdentity.pid));
    await assert.rejects(access('/proc/'+batch.pid));
    assert.equal(evidence.fixtureExit,mode==='abort'?0:143);
  }finally{process.exitCode=oldExit;await rm(root,{recursive:true});}
});

test('journey compatibility: repeatable mouse/keyboard login, diagnostics and selected interpreter',async()=>{
  const {realpath,readFile}=await import('node:fs/promises');
  await testStorage(storage);
  const root=await mkdtemp(path.join(storage,'runtime-compatibility-'));
  const work=path.join(root,'work');await mkdir(work);
  // Launch the configured entry in place: aliasing a venv executable into this
  // temporary directory would lose its pyvenv.cfg and installed dependencies.
  const selectedPython=process.env.PCH_PYTHON_EXECUTABLE||'/usr/bin/python3';
  assert.ok(path.isAbsolute(selectedPython));
  const previousPython=process.env.PCH_PYTHON_EXECUTABLE,oldExit=process.exitCode;
  process.env.PCH_PYTHON_EXECUTABLE=selectedPython;
  const browser=fakeBrowser(),page=browser.contexts()[0].pages()[0];
  const inputs=[],presses=[],clicks=[];let connectChecks=0,disconnectWaits=0;
  page.getByRole=(_role,{name})=>({
    click:async()=>clicks.push(name),
    evaluate:async()=>++connectChecks>1,
    waitFor:async()=>{assert.equal(name,'断开所有者');disconnectWaits++;},
  });
  page.getByLabel=()=>({fill:async value=>inputs.push(value),waitFor:async()=>{},evaluate:async()=>true});
  page.keyboard={press:async key=>presses.push(key),insertText:async value=>inputs.push(value)};
  const consoleMessage=(type,text)=>({type:()=>type,text:()=>text,location:()=>({url:'http://fixture/assets/test.js'})});
  const session=browser.newBrowserCDPSession;
  browser.newBrowserCDPSession=async()=>{
    const control=await session();
    return {send:async command=>{page.emit('console',consoleMessage('error','expected teardown diagnostic'));await control.send(command);}};
  };
  try{
    const evidence=await runBrowserJourney('compatibility',async({loginOwner,evidence,evidenceRoot})=>{
      assert.deepEqual(evidence.consoleWarnings,[]);
      assert.deepEqual(evidence.browserTransport,{consoleErrors:[],requestFailed:[]});
      await loginOwner();await loginOwner();await loginOwner({keyboard:true});
      assert.equal(inputs.length,3);assert.ok(inputs.every(value=>value===inputs[0]));
      assert.deepEqual(presses,['Tab','Enter','Enter']);
      assert.deepEqual(clicks,['连接所有者','认证','连接所有者','认证']);
      assert.equal(disconnectWaits,3);
      page.emit('console',consoleMessage('warning','representative warning'));
      page.emit('console',consoleMessage('error','representative transport diagnostic'));
      page.emit('requestfailed',{method:()=> 'GET',url:()=> 'http://fixture/api/test',failure:()=>({errorText:'net::ERR_ABORTED'})});
      assert.deepEqual(evidence.consoleWarnings,['representative warning']);
      assert.equal(evidence.browserTransport.consoleErrors[0].phase,'journey');
      assert.equal(evidence.browserTransport.requestFailed[0].reason,'net::ERR_ABORTED');
      const identity=JSON.parse(await readFile(path.join(evidenceRoot,'active-fixture.json'),'utf8'));
      assert.equal(identity.python_executable,await realpath(selectedPython));
      assert.equal(evidence.pythonExecutable,await realpath(selectedPython));
    },{storageRoot:work,evidenceRoot:path.join(root,'evidence'),cdp:'http://127.0.0.1:19999',
      connectBrowser:async()=>browser,timeoutMs:15000,cleanupTimeoutMs:5000});
    assert.equal(evidence.result,'PASS');assert.equal(evidence.workspaceRemoved,true);
    assert.equal(evidence.browserTransport.consoleErrors.at(-1).phase,'teardown');
    assert.deepEqual(await readdir(work),[]);
  }finally{
    if(previousPython===undefined)delete process.env.PCH_PYTHON_EXECUTABLE;else process.env.PCH_PYTHON_EXECUTABLE=previousPython;
    process.exitCode=oldExit;await rm(root,{recursive:true});
  }
});

for(const signalError of ['ESRCH','EPERM'])test(`verified group signal ${signalError} race preserves independent cleanup`,async()=>{
  await testStorage(storage);
  const root=await mkdtemp(path.join(storage,'runtime-signal-race-'));
  const work=path.join(root,'work');await mkdir(work);
  const script=path.join(root,'fixture.cjs');await writeFile(script,fixtureSource);
  const listeners=['SIGINT','SIGTERM'].map(event=>process.listenerCount(event));
  const browser=fakeBrowser();let origin,signalCalls=0;
  const oldExit=process.exitCode;
  try{
    const evidence=await runBrowserJourney('signal-race',async info=>{
      origin=info.origin;await new Promise(()=>{});
    },{storageRoot:work,evidenceRoot:path.join(root,'evidence'),cdp:'http://127.0.0.1:19999',
      fixtureCommand:[process.execPath,script,'stubborn'],connectBrowser:async()=>browser,
      timeoutMs:250,cleanupTimeoutMs:100,
      signalProcess:(pid,terminationSignal)=>{
        assert.ok(pid<0,'group identity was verified before this simulated race');
        signalCalls++;
        // The process exits after identity validation, before the signal result.
        process.kill(pid,'SIGKILL');
        throw Object.assign(new Error('simulated signal race'),{code:signalError});
      }});
    assert.ok(signalCalls>0);assert.equal(evidence.fixtureTerminal,true);
    assert.equal(evidence.fixtureGroupTerminal,true);assert.equal(evidence.workspaceRemoved,true);
    assert.equal(await portClosed(origin),true);
    assert.deepEqual(await readdir(work),[]);
    assert.deepEqual(browser.counts(),{closed:1,disconnected:1});
    assert.deepEqual(['SIGINT','SIGTERM'].map(event=>process.listenerCount(event)),listeners);
    assert.ok((await readdir(path.join(root,'evidence'))).includes('signal-race-browser.json'));
    if(signalError==='ESRCH')assert.equal(evidence.cleanupSignalErrors,undefined);
    else assert.deepEqual(evidence.cleanupSignalErrors,[{code:'EPERM'}]);
    assert.equal(evidence.result,'FAIL');
  }finally{process.exitCode=oldExit;await rm(root,{recursive:true});}
});

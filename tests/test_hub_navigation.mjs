import assert from 'node:assert/strict';
import test from 'node:test';
import {parseHubRoute,errorRecovery,api} from '../src/hub/web/common.js';

test('project deep links keep section identity separate from public directory query',()=>{
  const directory=parseHubRoute('#projects?q=known+alias&status=active&view=tree');
  assert.equal(directory.kind,'projects');assert.equal(directory.parts[1],undefined);
  assert.equal(directory.query.get('q'),'known alias');assert.equal(directory.query.get('view'),'tree');
  const detail=parseHubRoute('#projects/fixture-project-a/tasks');
  assert.deepEqual(detail.parts,['projects','fixture-project-a','tasks']);
  assert.equal(parseHubRoute('#projects/%broken').parts[1],'');
});

test('identity, permission, missing object, conflict, service, timeout and offline have distinct recovery',()=>{
  const errors=[{status:401},{status:403},{status:404},{status:409},{status:503},
    {code:'NETWORK_TIMEOUT'},{code:'NETWORK_UNAVAILABLE',status:0},{status:500}];
  assert.equal(new Set(errors.map(e=>errorRecovery(e).title)).size,errors.length);
  assert.match(errorRecovery({status:409}).message,/输入保留/);
  assert.match(errorRecovery({code:'NETWORK_TIMEOUT'}).message,/原请求/);
});

test('unknown mutation receipts are never replayed and bounded timeout retains effect uncertainty',async()=>{
  const original={fetch:globalThis.fetch,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
  const requests=[];
  try{
    globalThis.fetch=async(url,options)=>{
      requests.push(url);
      if(url==='/api/guest')return {ok:true,status:200,json:async()=>({ok:true,data:{csrf_token:'synthetic-csrf'}})};
      if(url==='/api/read')return {ok:true,status:200,json:async()=>({ok:true,data:{value:1}})};
      return {ok:false,status:401,json:async()=>({ok:false,error:{code:'SESSION_REQUIRED',outcome:'UNKNOWN'}})};
    };
    await api('/api/read');
    await assert.rejects(api('/api/mutation',{request_id:'owned-one'}),e=>e.outcome==='UNKNOWN');
    assert.deepEqual(requests,['/api/guest','/api/read','/api/mutation']);
    globalThis.setTimeout=callback=>{queueMicrotask(callback);return 1;};globalThis.clearTimeout=()=>{};
    globalThis.fetch=(_url,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));
    await assert.rejects(api('/api/read'),e=>e.code==='NETWORK_TIMEOUT'&&e.outcome==='NOT_COMMITTED');
    await assert.rejects(api('/api/mutation',{request_id:'owned-two'}),e=>e.code==='NETWORK_TIMEOUT'&&e.outcome==='UNKNOWN');
  }finally{Object.assign(globalThis,original);}
});

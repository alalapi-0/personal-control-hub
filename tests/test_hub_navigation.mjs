import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {parseHubRoute,errorRecovery,api} from '../src/hub/web/common.js';

test('project deep links keep section identity separate from public directory query',()=>{
  const directory=parseHubRoute('#projects?q=known+alias&status=active&view=tree');
  assert.equal(directory.kind,'projects');assert.equal(directory.parts[1],undefined);
  assert.equal(directory.query.get('q'),'known alias');assert.equal(directory.query.get('view'),'tree');
  const detail=parseHubRoute('#projects/fixture-project-a/tasks');
  assert.deepEqual(detail.parts,['projects','fixture-project-a','tasks']);
  assert.equal(parseHubRoute('#projects/%broken').parts[1],'');
});

test('late RR feedback response cannot replace a newer project page or its context',async()=>{
  const node=(tag,attrs={},...children)=>({tag,value:'',...attrs,children,
    append(...items){this.children.push(...items);},prepend(...items){this.children.unshift(...items);},
    replaceChildren(...items){this.children=items;},addEventListener(){},setAttribute(){},focus(){}});
  const main=node('main'),notice=node('notice'),footer=node('pre');
  const location={hash:'#projects'};let hold=false,release;
  const envelope=revision=>({revision,observed_at:'2026-10-19T00:00:00Z',data:{projects:[],total:0}});
  const response=value=>({ok:true,json:async()=>value});
  const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,location,
    localStorage:{getItem(){return null;}},el:node,button:(label,fn,attrs)=>node('button',attrs,label),
    document:{querySelector:selector=>({'#main':main,'#notice':notice,'#rr-context':footer}[selector])},
    window:{addEventListener(){},scrollTo(){}},
    fetch:async path=>{
      if(path.endsWith('/manifest'))return response({source:{},resources:[]});
      if(hold)return new Promise(resolve=>{release=()=>resolve(response(envelope('old-feedback')));});
      return response(envelope('current-projects'));
    }});
  const source=readFileSync(new URL('../src/hub/web/rr.js',import.meta.url),'utf8');
  vm.runInContext(source.replace(/^import [^\n]+\n/,''),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(main.children[0].children[0],'项目审核');
  hold=true;location.hash='#feedback';const old=vm.runInContext('route()',context);
  assert.equal(typeof release,'function');
  hold=false;location.hash='#projects';await vm.runInContext('route()',context);
  const currentContext=footer.textContent;
  release();await old;
  assert.equal(main.children[0].children[0],'项目审核');
  assert.equal(footer.textContent,currentContext);
  assert.match(footer.textContent,/current-projects/);
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

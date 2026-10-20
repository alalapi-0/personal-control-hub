import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=readFileSync(new URL('../src/hub/web/rr.js',import.meta.url),'utf8')
  .replace("import {el,button} from '/__rr/v1/resources/pch-dom';",'const {el,button}=helpers;');
const projectPath='/__rr/review/projects/personal-control-hub';

class Element {
  constructor(tag,attrs={},...children){this.tag=tag;Object.assign(this,attrs);this.children=children;this.value??='';}
  append(...children){this.children.push(...children);}
  prepend(...children){this.children.unshift(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(){}
  setAttribute(){}
  focus(){}
}
const el=(...args)=>new Element(...args);
const project={project_id:'personal-control-hub',name:'示例 PCH',status:'active',
  definition:Object.fromEntries(['purpose','outputs','aliases'].map(key=>[key,{value:'公开示例'}])),
  work:{state:'current',fields:{},progress:{completed:0,total:1},blockers:[],blockers_available:true}};

async function page(pathname,hash=''){
  const main=el('main'),notice=el('aside'),context=el('pre'),requests=[],events={};
  const location={pathname,hash};
  const sandbox={helpers:{el,button:(label)=>el('button',{},label)},location,
    document:{querySelector:selector=>({'#main':main,'#notice':notice,'#rr-context':context})[selector]},
    window:{addEventListener:(name,listener)=>{events[name]=listener;},scrollTo(){}},
    localStorage:{getItem:()=>null},AbortController,setTimeout,clearTimeout,
    fetch:async path=>{
      requests.push(path);
      const manifest={project_id:'personal-control-hub',source:{},page_paths:['/__rr/review/',projectPath],resources:[]};
      const data=path==='/__rr/v1/manifest'?manifest:path.endsWith('/tasks')?{data:{tasks:[]}}:
        path==='/__rr/v1/data/projects/personal-control-hub'?{data:project}:
        path==='/__rr/v1/data/projects'?{data:{projects:[project],total:1}}:null;
      return {ok:data!==null,status:data===null?404:200,json:async()=>data??{code:'RR_NOT_FOUND'}};
    }};
  await vm.runInNewContext(source,sandbox);
  return {main,requests,location,navigate:async hash=>{location.hash=hash;await events.hashchange();}};
}
const headings=page=>page.main.children.filter(node=>node.tag==='h1').map(node=>node.children.join(''));

test('direct detail URL and a fresh reload request/render the detail without directory fallback',async()=>{
  for(let reload=0;reload<2;reload++){
    const p=await page(projectPath);
    assert.deepEqual(headings(p),['示例 PCH']);
    assert.deepEqual(p.requests,['/__rr/v1/manifest','/__rr/v1/data/projects/personal-control-hub','/__rr/v1/data/projects/personal-control-hub/tasks']);
  }
});

test('explicit hash retains precedence and return navigation from a deep URL works',async()=>{
  const p=await page(projectPath,'#projects');
  assert.deepEqual(headings(p),['项目审核']);
  await p.navigate('#projects/personal-control-hub');
  assert.deepEqual(headings(p),['示例 PCH']);
  await p.navigate('#projects');
  assert.deepEqual(headings(p),['项目审核']);
});

test('root URL retains the directory and existing hash detail navigation',async()=>{
  const p=await page('/__rr/review/');
  assert.deepEqual(headings(p),['项目审核']);
  await p.navigate('#projects/personal-control-hub');
  assert.deepEqual(headings(p),['示例 PCH']);
});

test('unknown or noncanonical pathname is rejected before any project request',async()=>{
  for(const path of ['/__rr/review/projects/unknown',projectPath+'/extra','/__rr/review/projects/%70ersonal-control-hub']){
    const p=await page(path);
    assert.deepEqual(headings(p),['项目暂不可用']);
    assert.deepEqual(p.requests,['/__rr/v1/manifest']);
  }
});

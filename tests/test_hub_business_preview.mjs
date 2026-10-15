import assert from 'node:assert/strict';
import test from 'node:test';
import {businessPreviewState,renderBusinessPreview} from '../src/hub/web/business_preview.mjs';

const keys=['goal','obstacle','action','choice','consequence','difference','strength','weakness'];
const fixture=()=>({kind:'story_exchange',classification:'real',current:true,
  binding:{workspace_revision:51,workspace_sha256:'a'.repeat(64),request_id:'story-request-1',request_sha256:'b'.repeat(64),output_sha256:'c'.repeat(64)},
  cards:Array.from({length:3},(_,i)=>({id:`candidate-${i+1}`,title:`故事 ${i+1}`,...Object.fromEntries(keys.map(key=>[key,`${key} 的真实候选内容`]))}))});

test('valid real output distinguishes current and historical without changing input',()=>{
  const value=fixture(),before=structuredClone(value);
  assert.equal(businessPreviewState(value).state,'current');
  const result=businessPreviewState({...value,current:false});
  assert.equal(result.state,'history');assert.equal(result.cards.length,3);
  result.cards[0].title='changed';result.binding.request_id='changed';
  assert.deepEqual(value,before);
});

test('missing, simulated and malformed bindings stay unverified with no candidate cards',async t=>{
  const cases=[null,{}, {...fixture(),classification:'synthetic'}, {...fixture(),kind:'other'},
    {...fixture(),current:undefined}, {...fixture(),current:'true'}, {...fixture(),binding:null}];
  for(const key of ['workspace_revision','workspace_sha256','request_id','request_sha256','output_sha256']){
    const value=fixture();delete value.binding[key];cases.push(value);
  }
  for(const revision of [-1,1.5,Number.MAX_SAFE_INTEGER+1,'51'])cases.push({...fixture(),binding:{...fixture().binding,workspace_revision:revision}});
  for(const key of ['workspace_sha256','request_sha256','output_sha256'])cases.push({...fixture(),binding:{...fixture().binding,[key]:'invalid'}});
  for(const [index,value] of cases.entries())await t.test(String(index),()=>{
    const result=businessPreviewState(value);assert.equal(result.state,'unverified');assert.deepEqual(result.cards,[]);assert.ok(result.reason);
  });
});

test('exactly three unique fully populated textual cards required',()=>{
  for(const length of [0,1,2,4])assert.equal(businessPreviewState({...fixture(),cards:fixture().cards.slice(0,length).concat(length===4?[fixture().cards[0]]:[])}).state,'unverified');
  for(const key of ['id','title',...keys])for(const bad of [undefined,null,42,{},'', ' \n ']){
    const value=fixture();value.cards[1][key]=bad;assert.equal(businessPreviewState(value).state,'unverified',key);
  }
  const duplicate=fixture();duplicate.cards[1].id=duplicate.cards[0].id;
  assert.equal(businessPreviewState(duplicate).state,'unverified');
});

class FakeNode{
  constructor(tag,text=null){this.tag=tag;this.text=text;this.children=[];this.attrs={};}
  appendChild(child){this.children.push(child);return child;}
  append(...children){for(const child of children)this.children.push(typeof child==='string'?new FakeNode('#text',child):child);}
  setAttribute(key,value){this.attrs[key]=value;}
  get textContent(){return this.text??this.children.map(child=>child.textContent).join('');}
}
const all=node=>[node,...node.children.flatMap(all)];
function withDOM(run){
  const previous=globalThis.document;
  globalThis.document={createElement:tag=>new FakeNode(tag),createTextNode:text=>new FakeNode('#text',text)};
  try{run();}finally{globalThis.document=previous;}
}

test('readonly semantic cards preserve full long text and literal markup without executable nodes',()=>withDOM(()=>{
  const value=fixture(),markup='<img src=x onerror=alert(1)><script>bad()</script>',long='完整正文\n'.repeat(3000);
  value.cards[0].title=markup;value.cards[0].action=long;
  const rendered=renderBusinessPreview(value),nodes=all(rendered);
  assert.equal(rendered.tag,'section');assert.equal(rendered.attrs['aria-label'],'本次故事候选');
  assert.equal(nodes.filter(node=>node.tag==='article').length,3);
  assert.equal(nodes.filter(node=>node.tag==='h3').length,3);
  assert.equal(nodes.filter(node=>node.tag==='dt').length,28);
  assert.ok(nodes.some(node=>node.tag==='dd'&&node.textContent===long));
  assert.ok(rendered.textContent.includes(markup));
  assert.ok(nodes.every(node=>!['script','img','button','form','input','iframe'].includes(node.tag)));
  assert.match(rendered.textContent,/工作台版本：51/);
  assert.match(rendered.textContent,/尚未成为正式分集或世界事实/);
  const diagnostics=nodes.find(node=>node.tag==='details');
  assert.ok(diagnostics);assert.equal(diagnostics.attrs.open,undefined);
  for(const hash of [value.binding.workspace_sha256,value.binding.request_sha256,value.binding.output_sha256])assert.ok(diagnostics.textContent.includes(hash));
  for(const node of rendered.children.filter(node=>node!==diagnostics))assert.ok(!node.textContent.includes(value.binding.workspace_sha256));
}));

test('history stays visible as historical and invalid or empty output never renders result cards',()=>withDOM(()=>{
  const historical=renderBusinessPreview({...fixture(),current:false});
  assert.match(historical.textContent,/历史结果：已不属于当前工作台版本/);
  assert.ok(!historical.textContent.includes('当前工作台版本的真实业务产出'));
  for(const value of [null,{...fixture(),cards:[]}]){
    const rendered=renderBusinessPreview(value);
    assert.ok(all(rendered).every(node=>node.tag!=='article'));assert.match(rendered.textContent,/核实/);
  }
}));

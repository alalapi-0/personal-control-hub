import {el} from './common.js';

const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const text=value=>typeof value==='string'&&value.trim().length>0;
const fields=[['goal','目标'],['obstacle','阻力'],['action','行动'],['choice','选择'],
  ['consequence','后果'],['difference','与其他方向的区别'],['strength','可取之处'],['weakness','待改进之处']];

// The backend owns provenance verification. The display still refuses incomplete
// bindings, simulated results and malformed cards instead of claiming acceptance.
export function businessPreviewState(preview){
  const unverified=reason=>({state:'unverified',reason,binding:null,cards:[]});
  if(!preview||typeof preview!=='object'||Array.isArray(preview))return unverified('尚无可核实的本次故事候选。');
  if(preview.kind!=='story_exchange'||preview.classification!=='real')return unverified('当前结果尚未核实为真实业务产出。');
  const binding=preview.binding;
  if(!binding||typeof binding!=='object'||Array.isArray(binding)
    ||!Number.isSafeInteger(binding.workspace_revision)||binding.workspace_revision<0
    ||!text(binding.request_id)
    ||!['workspace_sha256','request_sha256','output_sha256'].every(key=>hash(binding[key])))return unverified('故事结果的工作台版本或来源绑定不完整。');
  if(typeof preview.current!=='boolean')return unverified('故事结果是否属于当前工作台版本尚未核实。');
  if(!Array.isArray(preview.cards)||preview.cards.length!==3
    ||!preview.cards.every(card=>card&&typeof card==='object'&&!Array.isArray(card)
      &&text(card.id)&&text(card.title)&&fields.every(([key])=>text(card[key])))
    ||new Set(preview.cards.map(card=>card.id)).size!==3)return unverified('本次应有三个内容完整且身份不同的故事方向；当前结果待核实。');
  return {state:preview.current?'current':'history',reason:null,
    binding:{workspace_revision:binding.workspace_revision,workspace_sha256:binding.workspace_sha256,
      request_id:binding.request_id,request_sha256:binding.request_sha256,output_sha256:binding.output_sha256},
    cards:preview.cards.map(card=>Object.fromEntries(['id','title',...fields.map(([key])=>key)].map(key=>[key,card[key]])))};
}

export function renderBusinessPreview(preview){
  const view=businessPreviewState(preview);
  const section=el('section',{className:'panel stack','aria-label':'本次故事候选'},el('h2',{},'本次故事候选'));
  if(view.state==='unverified'){
    section.append(el('p',{className:'warning'},view.reason));
    return section;
  }
  section.append(el('p',{className:view.state==='history'?'warning':'caption'},
    view.state==='history'?'历史结果：已不属于当前工作台版本，仅供回看。':'当前工作台版本的真实业务产出，三个方向待比较。'),
  el('p',{className:'caption'},'这些是待比较的故事候选，尚未成为正式分集或世界事实。'),
  el('p',{},`工作台版本：${view.binding.workspace_revision}`));
  for(const [index,card] of view.cards.entries()){
    const details=el('dl',{});
    for(const [key,label] of fields)details.append(el('div',{},el('dt',{},label),el('dd',{},card[key])));
    section.append(el('article',{className:'panel stack'},el('h3',{},`方向 ${index+1} · ${card.title}`),details));
  }
  const diagnostics=el('details',{className:'diagnostics'},el('summary',{},'查看结果来源绑定'));
  const binding=el('dl',{});
  for(const [key,label] of [['request_id','请求标识'],['workspace_sha256','工作台内容版本'],
    ['request_sha256','请求内容版本'],['output_sha256','结果内容版本']]){
    binding.append(el('div',{},el('dt',{},label),el('dd',{},view.binding[key])));
  }
  diagnostics.append(binding);
  section.append(diagnostics);
  return section;
}

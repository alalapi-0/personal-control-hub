import {el} from './common.js';

const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const text=value=>typeof value==='string'&&value.length>0;
const count=value=>value===null||(Number.isSafeInteger(value)&&value>=0);
const bool=value=>value===null||typeof value==='boolean';
const array=value=>Array.isArray(value)&&value.length<=10000;
const kinds={project:'项目',stage:'阶段',round:'轮次',leaf:'验收任务'};
const changeKinds={add:'新增',remove:'移除',split:'拆分',cancel:'取消',revise:'修订'};
const statuses={not_started:'未开始',active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',cancelled:'已取消',unknown:'未知'};
const selector=value=>{
  if(!value||typeof value!=='object'||Array.isArray(value))return false;
  const keys=Object.keys(value);
  if(keys.length===1&&keys[0]==='path')return array(value.path)&&value.path.length>0&&value.path.length<=20&&value.path.every(part=>text(part)||(Number.isSafeInteger(part)&&part>=0));
  if(keys.length!==2||!text(value.heading))return false;
  return keys.includes('label')?text(value.label):keys.includes('table_label')?text(value.table_label):keys.includes('ordered_item')&&Number.isSafeInteger(value.ordered_item)&&value.ordered_item>0;
};
const countNames=['total','implemented','verified','human','not_started','active','blocked','cancelled'];

// The server owns complete graph/history validation. This display guard rejects
// obvious malformed contracts; it never manufactures a tree from other data.
function displayable(plan,projectId){
  if(plan?.schema_version!=='1.0'||plan.project_id!==projectId
    ||!['plan_version','unit','counting_basis'].every(key=>text(plan[key]))
    ||!hash(plan.catalog_sha256)||!hash(plan.input_version)||!array(plan.nodes)
    ||!plan.labels||typeof plan.labels!=='object'||Array.isArray(plan.labels)
    ||!array(plan.acceptance)||!plan.counts||!countNames.every(key=>count(plan.counts[key]))
    ||!count(plan.baseline_denominator))return false;
  const nodes=new Map(plan.nodes.map(node=>[node?.id,node]));
  if(nodes.size!==plan.nodes.length||!plan.nodes.every(node=>text(node?.id)&&text(plan.labels[node.id])
    &&Object.hasOwn(kinds,node.kind)&&Object.hasOwn(statuses,node.status)
    &&(node.condition===null||text(node.condition))&&array(node.evidence)&&node.evidence.every(text)
    &&array(node.dependencies)&&node.dependencies.every(text)
    &&(node.parent===null||nodes.has(node.parent))))return false;
  const roots=plan.nodes.filter(node=>node.parent===null);
  if(plan.nodes.length&& (roots.length!==1||roots[0].kind!=='project'||roots[0].id!==projectId))return false;
  const parentKind={stage:'project',round:'stage',leaf:'round'};
  if(!plan.nodes.every(node=>node.kind==='project'?node.parent===null:node.parent!==null&&nodes.get(node.parent)?.kind===parentKind[node.kind]))return false;
  const leaves=plan.nodes.filter(node=>node.kind==='leaf'),leafIds=new Set(leaves.map(node=>node.id));
  if(plan.counts.total!==null&&plan.counts.total!==leaves.length)return false;
  if(countNames.slice(1).some(key=>plan.counts.total!==null&&plan.counts[key]!==null&&plan.counts[key]>plan.counts.total))return false;
  if(plan.acceptance.length!==leaves.length||new Set(plan.acceptance.map(row=>row?.task_id)).size!==leaves.length
    ||!plan.acceptance.every(row=>leafIds.has(row?.task_id)&&text(row.plan_version)
      &&(!['implemented','verified','human'].some(key=>row[key]===true)||row.plan_version===plan.plan_version)
      &&hash(row.condition_version)&&['implemented','verified','human'].every(key=>bool(row[key]))
      &&(row.evidence_version===null||text(row.evidence_version))&&array(row.evidence_refs)&&row.evidence_refs.every(text)
      &&(row.unknown_reason===null||text(row.unknown_reason))
      &&(!['implemented','verified','human'].some(key=>row[key]===null)||text(row.unknown_reason))
      &&(!row.verified||(row.implemented===true&&text(row.evidence_version)&&row.evidence_refs.length>0))
      &&(!row.human||row.verified===true)))return false;
  if(leaves.some(node=>node.status==='cancelled'&&plan.acceptance.some(row=>row.task_id===node.id&&(row.verified===true||row.human===true))))return false;
  if(plan.history===null)return text(plan.history_unknown_reason);
  const history=plan.history;
  return text(history?.baseline_plan_version)&&array(history.baseline_leaf_ids)&&history.baseline_leaf_ids.every(text)
    &&array(history.changes)&&history.changes.every(change=>text(change?.from_plan_version)&&text(change.to_plan_version)
      &&Object.hasOwn(changeKinds,change.kind)&&text(change.reason)&&array(change.before_leaf_ids)&&change.before_leaf_ids.every(text)
      &&array(change.after_leaf_ids)&&change.after_leaf_ids.every(text)&&array(change.evidence_refs)&&change.evidence_refs.every(text)
      &&array(change.mapping)&&change.mapping.every(mapping=>array(mapping?.old_ids)&&mapping.old_ids.every(text)&&array(mapping.new_ids)&&mapping.new_ids.every(text)))
    &&plan.history_unknown_reason===null;
}

export function businessPlan(project,{history=false}={}){
  const result={state:'unavailable',history,reason:'当前业务计划证据不可用。',plan:null,progress:null,observedAt:null,source:null};
  const removed=project?.freshness?.local_presence==='removed_local'||project?.freshness?.state==='removed_local';
  if(removed||project?.declared?.connection_read_allowed!==true)return {...result,reason:'按当前读取权限，业务计划不可查看。'};
  const record=history?project?.operational?.last_success:project?.operational?.latest_attempt;
  if(!history&&record?.success===false)return {...result,state:'failed',reason:'最新业务来源读取失败；历史计划不能替代当前计划。'};
  if(!history&&(project?.freshness?.state!=='fresh'||project.freshness.authority_drift===true))return {...result,state:'stale',reason:'来源尚未更新或规则已变化；当前业务计划失效。'};
  if(record?.schema_version!=='2.0'||record.success!==true||record.project_id!==project.project_id||!text(record.observed_at))return result;
  const extension=record.extensions;
  if(extension!==null&&extension!==undefined&&(!['1.0','1.1','1.2','1.3'].includes(extension.schema_version)||!extension.fact||typeof extension.fact!=='object'||Array.isArray(extension.fact)))return result;
  if(extension===null||extension===undefined||!Object.hasOwn(extension.fact,'task_plan'))return {...result,state:'not_declared',reason:'来源尚未声明业务任务计划；不会从旧进度或执行任务补建任务树。'};
  if(!['1.1','1.2','1.3'].includes(extension.schema_version))return result;
  const field=extension.fact.task_plan;
  if(field?.value===null)return {...result,reason:text(field.unknown_reason)?field.unknown_reason:'来源未提供业务任务计划。'};
  const proof=field?.proof;
  const sources=record.sources;
  if(!hash(proof?.sha256)||!text(proof.source_ref)||proof.observed_at!==record.observed_at
    ||!selector(proof.selector)
    ||!Array.isArray(sources)||sources.filter(source=>source.id===proof.source_ref&&source.sha256===proof.sha256
      &&(source.observed_at===undefined||source.observed_at===record.observed_at)).length!==1
    ||field.unknown_reason!==null||!displayable(field.value,project.project_id))return result;
  const plan=structuredClone(field.value),n=plan.counts.verified,total=plan.counts.total;
  const reason=n===null||total===null?'unknown_count':total===0?'not_applicable':null;
  return {...result,state:plan.nodes.every(node=>node.kind!=='leaf')?'empty':history?'history':'current',reason:null,plan,
    progress:{numerator:n,denominator:total,ratio:reason===null?n/total:null,reason,
      unit:plan.unit,plan_version:plan.plan_version,counting_basis:plan.counting_basis,baseline_denominator:plan.baseline_denominator},
    observedAt:record.observed_at,source:{source_ref:proof.source_ref,sha256:proof.sha256,selector:structuredClone(proof.selector)}};
}

const known=value=>value===null?'未知':String(value);
const accepted=value=>value===null?'未知':value?'是':'否';
const refs=values=>values.length?el('ul',{className:'source-list'},...values.map(value=>el('li',{},value))):el('p',{className:'caption'},'来源未列出证据。');

export function renderBusinessPlan(project,options={}){
  const view=businessPlan(project,options);
  const section=el('section',{className:'panel stack','aria-label':options.history?'历史业务计划任务':'业务计划任务'},
    el('h2',{},options.history?'业务计划任务 · 历史快照':'业务计划任务'));
  if(!view.plan){section.append(el('p',{className:['failed','stale','unavailable'].includes(view.state)?'warning':'caption'},view.reason));return section;}
  const plan=view.plan,progress=view.progress,leaves=plan.nodes.filter(node=>node.kind==='leaf');
  section.append(el('p',{className:view.history?'warning':'caption'},view.history?'历史版本与证据，仅供回看，不代表当前验收。':'来源明确声明的业务计划；与后台执行任务分开。'),
    el('p',{},`计划版本：${plan.plan_version} · 读取观察时间：${view.observedAt}`),
    el('p',{},`业务验收 V/N：${known(progress.numerator)} / ${known(progress.denominator)} ${plan.unit}${progress.reason==='not_applicable'?' · 不适用（总量为零）':progress.reason==='unknown_count'?' · 比例未知':''}`),
    el('p',{className:'caption'},`计数依据：${plan.counting_basis} · 来源列出 ${leaves.length} 个叶子任务`),
    el('p',{},`已实现 I：${known(plan.counts.implemented)} · 已验证 V：${known(plan.counts.verified)} · 用户验收 H：${known(plan.counts.human)}`),
    el('p',{className:'caption'},`叶子任务状态：未开始 ${known(plan.counts.not_started)} · 进行中 ${known(plan.counts.active)} · 受阻 ${known(plan.counts.blocked)} · 已取消 ${known(plan.counts.cancelled)}`));
  if(view.state==='empty')section.append(el('p',{className:'caption'},'来源明确声明没有业务叶子任务。总量为零时验收比例不适用；总量未知时仍保留未知。'));
  const acceptance=new Map(plan.acceptance.map(row=>[row.task_id,row]));
  // Parent-kind checks above limit this traversal to exactly four levels.
  // Dependencies remain server-validated and never create display children.
  const children=new Map(plan.nodes.map(node=>[node.id,[]]));
  for(const node of plan.nodes)if(node.parent!==null)children.get(node.parent).push(node);
  const hasActiveLeaf=new Set();
  const nodes=new Map(plan.nodes.map(node=>[node.id,node]));
  for(const leaf of leaves.filter(node=>node.status==='active')){
    let parent=leaf.parent;
    for(let depth=0;parent!==null&&depth<3;depth++){
      hasActiveLeaf.add(parent);parent=nodes.get(parent).parent;
    }
  }
  function taskEntry(node,depth){
    // A malformed contract cannot initiate unrestricted recursion.
    if(depth>3)throw new Error('BUSINESS_PLAN_DISPLAY_DEPTH');
    const row=acceptance.get(node.id);
    const entry=el('details',{className:'stack business-task-node',open:node.kind==='project'||hasActiveLeaf.has(node.id)},
      el('summary',{className:'business-task-summary'},`${kinds[node.kind]} · ${plan.labels[node.id]} · ${statuses[node.status]}`),
      el('p',{className:'caption'},`任务 ID：${node.id}`),
      el('p',{className:'caption'},node.parent===null?'项目根':`所属：${plan.labels[node.parent]}（${node.parent}）`));
    if(row)entry.append(el('div',{className:'stack'},
      el('p',{className:'caption'},node.dependencies.length?`依赖任务：${node.dependencies.map(id=>`${id}（${plan.labels[id]??'标签未知'}）`).join('、')}`:'依赖任务：无依赖'),
      el('p',{},`验收条件：${node.condition??'未知 · 来源未提供'}`),
      el('p',{},`I 已实现：${accepted(row.implemented)} · V 已验证：${accepted(row.verified)} · H 用户验收：${accepted(row.human)}`),
      el('p',{className:'caption'},`适用计划版本：${row.plan_version} · 条件版本：${row.condition_version} · 证据版本：${row.evidence_version??'未知'}`),
      row.plan_version!==plan.plan_version?el('p',{className:'caption'},'旧版验收不适用于当前计划；不计为当前已验证。'):null,
      row.unknown_reason?el('p',{className:'caption'},'缺口与未知原因：'+row.unknown_reason):null,
      refs(row.evidence_refs),
      el('details',{},el('summary',{},'查看任务条件与证据版本'),el('p',{className:'caption'},`目录版本：${plan.catalog_sha256} · 输入版本：${plan.input_version}`),refs(node.evidence))));
    const childNodes=children.get(node.id);
    if(childNodes.length)entry.append(el('div',{className:'stack business-task-children'},...childNodes.map(child=>taskEntry(child,depth+1))));
    return entry;
  }
  for(const node of plan.nodes.filter(node=>node.parent===null))section.append(taskEntry(node,0));
  if(plan.history===null)section.append(el('p',{className:'caption'},`原始分母：${known(plan.baseline_denominator)} · 变更历史未知：${plan.history_unknown_reason}`));
  else{
    const history=plan.history;
    const changes=el('details',{},el('summary',{},'查看计划版本与分母变更'),
      el('p',{},`基线 ${history.baseline_plan_version}：${known(plan.baseline_denominator)} · 当前 ${plan.plan_version}：${known(plan.counts.total)}`));
    for(const change of history.changes)changes.append(el('p',{},`${change.from_plan_version} → ${change.to_plan_version} · ${changeKinds[change.kind]} · ${change.reason}`),
      el('p',{className:'caption'},`变更前 ${change.before_leaf_ids.length} 个叶子项 · 变更后 ${change.after_leaf_ids.length} 个叶子项`),
      ...change.mapping.map(mapping=>el('p',{className:'caption'},`任务对应：${mapping.old_ids.join('、')||'无'} → ${mapping.new_ids.join('、')||'无'}`)),refs(change.evidence_refs));
    if(!history.changes.length)changes.append(el('p',{className:'caption'},'来源未列出版本变更。'));
    section.append(changes);
  }
  return section;
}

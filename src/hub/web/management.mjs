const LEGACY_FIELDS=['priority','start_date','end_date','watch','order','layout','relations'];
const FIELDS=[...LEGACY_FIELDS,'timezone','milestones'];
const ROLLING_FIELDS=[...FIELDS,'rolling_plan'];
const copy=v=>structuredClone(v);
const blank=()=>Object.fromEntries(FIELDS.map(k=>[k,null]));
const localDraft=value=>({...copy(value??blank()),timezone:value?.timezone??null,milestones:copy(value?.milestones??null)});
function timezone(v){if(v===null)return true;if(typeof v!=='string'||!v||v.length>128||v.startsWith('/')||v.includes('..')||/[^A-Za-z0-9_+\-/]/.test(v))return false;try{new Intl.DateTimeFormat('en',{timeZone:v});return true;}catch{return false;}}
function object(v,keys){return v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));}
function date(v){if(v===null)return true;if(typeof v!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(v)||Number(v.slice(0,4))===0)return false;const d=new Date(`${v}T00:00:00Z`);return !Number.isNaN(d.getTime())&&d.toISOString().slice(0,10)===v;}
function rollingPlan(v){
  if(v===null)return true;
  if(!object(v,['schema_version','kind','as_of','next_review','completion_window','confidence','basis','conditions','next_milestone','acceptance_windows'])||v.schema_version!=='1.0'||v.kind!=='rolling_estimate'||!v.as_of||!date(v.as_of)||!v.next_review||!date(v.next_review)||v.next_review<v.as_of||!['unknown','low','medium','high'].includes(v.confidence))return false;
  const str=(x,max)=>typeof x==='string'&&x.trim().length>0&&x.length<=max;
  const id=x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(x);
  const range=x=>x===null||object(x,['earliest','latest'])&&Boolean(x.earliest)&&Boolean(x.latest)&&date(x.earliest)&&date(x.latest)&&x.earliest<=x.latest;
  if(!range(v.completion_window)||!Array.isArray(v.basis)||v.basis.length>20||v.basis.some(x=>!object(x,['kind','reference','sha256','summary'])||!['source','assumption'].includes(x.kind)||!str(x.reference,200)||!str(x.summary,500)||!(x.sha256===null&&x.kind==='assumption'||typeof x.sha256==='string'&&/^[a-fA-F0-9]{64}$/.test(x.sha256)))||!Array.isArray(v.conditions)||v.conditions.length>20||v.conditions.some(x=>!str(x,500)))return false;
  const m=v.next_milestone;
  if(m!==null&&(!object(m,['id','label','window','condition'])||!id(m.id)||!str(m.label,200)||!range(m.window)||m.condition!==null&&!str(m.condition,500)))return false;
  const a=v.acceptance_windows;
  if(!Array.isArray(a)||a.length>20||a.some(x=>!object(x,['id','kind','label','window','depends_on','scope'])||!id(x.id)||!['automatic','human'].includes(x.kind)||!str(x.label,200)||!range(x.window)||!str(x.scope,500)||!Array.isArray(x.depends_on)||x.depends_on.length>20||new Set(x.depends_on).size!==x.depends_on.length||x.depends_on.some(y=>!id(y))))return false;
  const ids=[...(m?[m.id]:[]),...a.map(x=>x.id)];if(new Set(ids).size!==ids.length)return false;
  const known=new Set(ids),edges=new Map(a.map(x=>[x.id,x.depends_on]));if(a.some(x=>x.depends_on.some(y=>y===x.id||!known.has(y))))return false;
  const visited=new Set(),active=new Set();function visit(key){if(active.has(key))return false;if(visited.has(key))return true;active.add(key);for(const next of edges.get(key)??[])if(!visit(next))return false;active.delete(key);visited.add(key);return true;}
  return ids.every(visit);
}
function validOverlay(v,id,options){
  if(!object(v,LEGACY_FIELDS)&&!object(v,FIELDS)&&!object(v,ROLLING_FIELDS))return false;
  if(Object.hasOwn(v,'rolling_plan')&&!rollingPlan(v.rolling_plan))return false;
  if(Object.hasOwn(v,'timezone')){if(!timezone(v.timezone))return false;const m=v.milestones;if(m!==null&&(!Array.isArray(m)||m.length>1000||new Set(m.map(x=>x?.id)).size!==m.length||m.some(x=>!object(x,['id','label','date'])||typeof x.id!=='string'||!x.id.trim()||typeof x.label!=='string'||!x.label.trim()||!date(x.date))))return false;}
  if(['priority','order'].some(k=>v[k]!==null&&(!Number.isSafeInteger(v[k])||v[k]<0)))return false;
  if(!date(v.start_date)||!date(v.end_date)||(v.start_date&&v.end_date&&v.start_date>v.end_date))return false;
  if(v.watch!==null&&typeof v.watch!=='boolean')return false;
  if(v.layout!==null&&(!object(v.layout,['x','y'])||!Number.isFinite(v.layout.x)||!Number.isFinite(v.layout.y)||v.layout.x<0||v.layout.x>320||v.layout.y<0||v.layout.y>160))return false;
  const r=v.relations;if(r===null)return true;
  if(!object(r,['belongs_to','depends_on','related_to','independent'])||r.independent!==null&&typeof r.independent!=='boolean')return false;
  const ref=x=>typeof x==='string'&&x!==id&&options.has(x);
  if(r.belongs_to!==null&&!ref(r.belongs_to))return false;
  return ['depends_on','related_to'].every(k=>Array.isArray(r[k])&&new Set(r[k]).size===r[k].length&&r[k].every(ref));
}
// Diagnostics explain the existing command contract; they do not relax it or
// turn graph warnings into additional client-side rejection rules.
export function managementFieldErrors(value,projectId,options=new Set()){
  const errors=[],add=(field,message)=>errors.push({field,message});
  if(!value||typeof value!=='object'||Array.isArray(value))return [{field:'form',message:'管理配置结构无效，请重新读取当前版本。'}];
  if(!object(value,LEGACY_FIELDS)&&!object(value,FIELDS)&&!object(value,ROLLING_FIELDS))add('form','管理配置字段不完整或包含未识别字段，请重新读取当前版本。');
  for(const [key,label] of [['start_date','开始日期'],['end_date','结束日期']])
    if(!date(value[key]))add(key,`${label}必须是有效的日历日期（年-月-日）；可留空表示未排期。`);
  if(date(value.start_date)&&date(value.end_date)&&value.start_date&&value.end_date&&value.start_date>value.end_date)
    add('end_date','结束日期不能早于开始日期，请调整结束或开始日期。');
  for(const [key,label] of [['priority','优先级'],['order','显示顺序']])
    if(value[key]!==null&&(!Number.isSafeInteger(value[key])||value[key]<0))add(key,`${label}须为非负整数；可留空。`);
  if(value.watch!==null&&typeof value.watch!=='boolean')add('watch','关注须选择是、否或未配置。');
  if(Object.hasOwn(value,'timezone')&&!timezone(value.timezone))add('timezone','请输入有效的 IANA 时区，例如 Asia/Tokyo；留空表示未知。');
  if(value.layout!==null){
    if(!object(value.layout,['x','y']))add('layout.x','布局坐标须同时包含 X 和 Y，请重新设置坐标。');
    else for(const [key,max] of [['x',320],['y',160]])if(!Number.isFinite(value.layout[key])||value.layout[key]<0||value.layout[key]>max)
      add('layout.'+key,`布局 ${key.toUpperCase()} 须为 0 至 ${max} 的有限数值。`);
  }
  if(Object.hasOwn(value,'milestones')&&value.milestones!==null){
    const milestones=value.milestones;
    if(!Array.isArray(milestones)||milestones.length>1000)add('milestones','计划里程碑须为列表，且不超过 1000 项；可切换为未配置。');
    else{
      const ids=new Set();
      milestones.forEach((milestone,index)=>{
        const prefix=`milestones.${index}.`;
        if(!object(milestone,['id','label','date'])){add('milestones',`第 ${index+1} 项里程碑结构无效，请删除并重新添加。`);return;}
        if(typeof milestone.id!=='string'||!milestone.id.trim()||ids.has(milestone.id))add('milestones',`第 ${index+1} 项里程碑标识为空或重复，请删除并重新添加。`);
        ids.add(milestone.id);
        if(typeof milestone.label!=='string'||!milestone.label.trim())add(prefix+'label','里程碑名称不能为空，请填写名称。');
        if(!date(milestone.date))add(prefix+'date','里程碑日期必须是有效的日历日期；留空表示未排期。');
      });
    }
  }
  const relation=value.relations;
  if(relation!==null){
    if(!object(relation,['belongs_to','depends_on','related_to','independent']))add('relations','关系配置结构无效，请重新选择关系配置。');
    else{
      if(relation.independent!==null&&typeof relation.independent!=='boolean')add('relations.independent','独立项目须选择是、否或未配置。');
      for(const [key,label] of [['belongs_to','归属项目'],['depends_on','依赖项目'],['related_to','关联项目']]){
        const values=key==='belongs_to'?(relation[key]===null?[]:[relation[key]]):relation[key];
        if(!Array.isArray(values)){add('relations.'+key,`${label}配置须为项目列表，请重新选择。`);continue;}
        if(new Set(values).size!==values.length)add('relations.'+key,`${label}包含重复项目，请重新选择且每个项目只保留一次。`);
        if(values.includes(projectId))add('relations.'+key,`${label}不能指向项目自身，请选择其他项目。`);
        if(values.some(id=>typeof id!=='string'||id!==projectId&&!options.has(id)))add('relations.'+key,`${label}包含当前不可用或未获允许的目标，请重新选择可用项目。`);
      }
    }
  }
  if(Object.hasOwn(value,'rolling_plan')&&!rollingPlan(value.rolling_plan))add('rolling_plan','只读滚动预估格式无效，请重新读取或在其管理入口修正。');
  return errors;
}
function validProposalRef(ref){
  return object(ref,['source_sha256','proposal_id','proposal_hash','registry_sha256'])
    &&typeof ref.proposal_id==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(ref.proposal_id)
    &&['source_sha256','proposal_hash','registry_sha256'].every(key=>typeof ref[key]==='string'&&/^[a-f0-9]{64}$/.test(ref[key]));
}
export function buildManagementCommand(view,projectId,operation,value,requestId,proposalRef=null){
  if(!view?.available||view.editable!==true||!Number.isSafeInteger(view.revision)||view.revision<0||typeof view.config_hash!=='string'||!/^[a-fA-F0-9]{64}$/.test(view.config_hash))throw new Error('MANAGEMENT_NOT_EDITABLE');
  if(typeof projectId!=='string'||!projectId||!Array.isArray(view.project_options)||!view.project_options.some(p=>p.id===projectId))throw new Error('MANAGEMENT_TARGET_INVALID');
  if(typeof requestId!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(requestId))throw new Error('MANAGEMENT_REQUEST_INVALID');
  if(operation!=='set'&&operation!=='retract'||operation==='retract'&&value!==null||operation==='set'&&!validOverlay(value,projectId,new Set(view.project_options.map(p=>p.id))))throw new Error('MANAGEMENT_VALUE_INVALID');
  if(proposalRef!==null&&(operation!=='set'||!validProposalRef(proposalRef)))throw new Error('MANAGEMENT_PROPOSAL_INVALID');
  return {request_id:requestId,expected_revision:view.revision,expected_hash:view.config_hash,project_id:projectId,operation,value:copy(value),
    ...(proposalRef===null?{}:{proposal_ref:copy(proposalRef)})};
}
function el(tag,attrs={},...children){const n=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k.startsWith('on'))n.addEventListener(k.slice(2).toLowerCase(),v);else if(k in n)n[k]=v;else n.setAttribute(k,String(v));}for(const c of children.flat()){if(c!==null&&c!==undefined)n.appendChild(typeof c==='string'?document.createTextNode(c):c);}return n;}
function text(n,s){n.textContent=s;}
function requestId(){return `management-${globalThis.crypto.randomUUID()}`;}
export function renderManagement(initial,{projectId,owner=false,save,onSaved,reload}={}){
  let view=copy(initial),draft=localDraft(initial?.overlay),busy=false,pending=null,conflict=false,requiresReload=false,proposalIntent=null;
  const controls=[],section=el('section',{className:'management-panel','aria-label':'Hub 管理配置'}),status=el('p',{className:'management-status',role:'status','aria-live':'polite','aria-label':'管理配置保存状态'}),revision=el('p',{className:'management-revision'}),form=el('fieldset',{className:'management-fields'});
  const permitted=()=>Boolean(owner&&view?.available&&view.editable&&Number.isSafeInteger(view.revision)&&view.revision>=0&&typeof view.config_hash==='string'&&/^[a-fA-F0-9]{64}$/.test(view.config_hash)&&Array.isArray(view.project_options)&&view.project_options.some(p=>p.id===projectId)&&typeof save==='function');
  const feedback=new Map(),errorPrefix=requestId(),validationSummary=el('p',{className:'management-validation warning',role:'status','aria-live':'polite'});let fieldSequence=0;
  const inputs={},warnings=el('ul',{className:'management-warnings'}),rollingSummary=el('div',{className:'management-note','aria-label':'滚动预估（只读）'});
  const proposalsPanel=el('section',{className:'stack','aria-label':'关系提议确认'}),historyPanel=el('section',{className:'stack','aria-label':'管理配置历史'}),proposalControls=[],historyControls=[];
  function fieldFeedback(node,key){
    const id=`${errorPrefix}-field-${++fieldSequence}`,error=el('span',{className:'management-field-error warning',id});
    error.hidden=true;node.setAttribute('aria-describedby',id);node.setAttribute('aria-invalid','false');
    feedback.set(key,{node,error});return error;
  }
  function field(label,node,key){
    node.setAttribute('aria-label',label);inputs[key]=node;controls.push(node);
    const path=key.startsWith('rel-')?'relations.'+key.slice(4):key.startsWith('layout-')?'layout.'+key.slice(7):key==='mode'?'relations':key==='milestone-mode'?'milestones':key;
    form.appendChild(el('label',{className:'management-field'},el('span',{},label),node,fieldFeedback(node,path)));return node;
  }
  function refreshValidation(){
    const errors=managementFieldErrors(draft,projectId,new Set((view?.project_options??[]).map(p=>p.id)));
    for(const [key,{node}] of feedback)if(node.validity?.badInput)
      errors.push({field:key,message:key==='start_date'||key==='end_date'||key.endsWith('.date')?'请输入完整有效的日历日期。':'请输入有效数值。'});
    for(const [key,{node,error}] of feedback){
      const messages=errors.filter(item=>item.field===key).map(item=>item.message);
      node.setAttribute('aria-invalid',String(messages.length>0));text(error,messages.join(' '));error.hidden=!messages.length;
    }
    text(validationSummary,errors.length?'请修改配置：'+errors.map(item=>item.message).join(' '):'');
    validationSummary.hidden=errors.length===0;return errors;
  }
  function scalar(key,label,type,write){const n=field(label,el('input',{type,min:type==='number'?'0':'',step:'1'}),key);n.addEventListener('input',()=>{const v=n.value===''?null:type==='number'?Number(n.value):n.value;if(write){write(v);sync();}else{draft[key]=v;refresh();}});}
  scalar('priority','优先级（数值）','number');scalar('start_date','开始日期','date');scalar('end_date','结束日期','date');scalar('order','显示顺序','number');
  const canChange=()=>permitted()&&!busy&&!pending&&!requiresReload;
  const zone=field('计划时区（IANA，可留空）',el('input',{type:'text',placeholder:'Asia/Tokyo',maxLength:128,autocomplete:'off'}),'timezone');
  zone.addEventListener('input',()=>{if(!canChange())return;draft.timezone=zone.value===''?null:zone.value;refresh();});
  const milestoneMode=field('计划里程碑配置',el('select',{},el('option',{value:'unconfigured'},'未配置'),el('option',{value:'configured'},'已配置（可为空）')),'milestone-mode');
  const milestoneList=el('div',{className:'management-milestones'}),milestoneControls=[];
  const addMilestone=el('button',{type:'button',className:'management-milestone-add',onClick:()=>{if(!canChange()||draft.milestones===null||draft.milestones.length>=1000)return;draft.milestones.push({id:`planned-${globalThis.crypto.randomUUID()}`,label:'新计划里程碑',date:null});syncMilestones();refresh();}},'添加计划里程碑');
  controls.push(addMilestone);
  milestoneMode.addEventListener('change',()=>{if(!canChange())return;draft.milestones=milestoneMode.value==='unconfigured'?null:[];syncMilestones();refresh();});
  form.appendChild(milestoneList);form.appendChild(addMilestone);
  form.appendChild(el('p',{className:'management-note'},'计划日期按所填时区的日历日期记录；时区留空表示未知。里程碑日期留空表示未排期；未配置与已配置的空列表不同。这些计划不会改写实际日期或完成情况。'));
  function syncMilestones(){for(const key of feedback.keys())if(key.startsWith('milestones.'))feedback.delete(key);milestoneMode.value=draft.milestones===null?'unconfigured':'configured';milestoneControls.length=0;milestoneList.replaceChildren();for(const [index,m] of (draft.milestones??[]).entries()){const row=el('div',{className:'management-milestone-row','data-milestone-id':m.id}),labelName=`计划里程碑名称（${m.id}）`,dateName=`计划里程碑日期（${m.id}）`,labelInput=el('input',{type:'text',value:m.label,'aria-label':labelName}),dateInput=el('input',{type:'date',value:m.date??'','aria-label':dateName}),remove=el('button',{type:'button','aria-label':`删除计划里程碑（${m.id}）`,onClick:()=>{if(!canChange())return;draft.milestones=draft.milestones.filter(x=>x.id!==m.id);syncMilestones();refresh();}},'删除计划里程碑');labelInput.addEventListener('input',()=>{if(!canChange())return;m.label=labelInput.value;refresh();});dateInput.addEventListener('input',()=>{if(!canChange())return;m.date=dateInput.value===''?null:dateInput.value;refresh();});milestoneControls.push(labelInput,dateInput,remove);row.appendChild(el('label',{className:'management-field'},el('span',{},labelName),labelInput,fieldFeedback(labelInput,`milestones.${index}.label`)));row.appendChild(el('label',{className:'management-field'},el('span',{},dateName),dateInput,fieldFeedback(dateInput,`milestones.${index}.date`)));row.appendChild(remove);milestoneList.appendChild(row);}}
  function tri(key,label,read,write){const n=field(label,el('select',{},el('option',{value:''},'未配置'),el('option',{value:'true'},'是'),el('option',{value:'false'},'否')),key);n.addEventListener('change',()=>{write(n.value===''?null:n.value==='true');refresh();});return n;}
  tri('watch','关注',()=>draft.watch,v=>draft.watch=v);
  const mode=field('关系配置',el('select',{},el('option',{value:'unconfigured'},'未配置'),el('option',{value:'confirmed'},'所有者确认的关系')),'mode');
  mode.addEventListener('change',()=>{draft.relations=mode.value==='unconfigured'?null:{belongs_to:null,depends_on:[],related_to:[],independent:null};sync();});
  function relation(key,label,multiple){const n=field(label,el('select',{multiple,'aria-label':label}),'rel-'+key);n.addEventListener('change',()=>{if(!draft.relations)return;draft.relations[key]=multiple?Array.from(n.selectedOptions).map(o=>o.value):n.value||null;refresh();});return n;}
  relation('belongs_to','归属项目',false);relation('depends_on','依赖项目（可多选）',true);relation('related_to','关联项目（可多选）',true);
  tri('independent','独立项目',()=>draft.relations?.independent,v=>{if(draft.relations)draft.relations.independent=v;});
  form.appendChild(el('p',{className:'management-note'},'“未配置”不会推断为独立。独立项目仍可依赖其他项目。关系仅代表所有者配置，不改变来源进度或执行任务。'));
  // Layout fields deliberately touch only the layout draft, never relations.
  for(const[axis,label]of [['x','布局 X'],['y','布局 Y']]){scalar('layout-'+axis,label,'number',v=>{draft.layout=v===null?null:{...(draft.layout??{x:160,y:80}),[axis]:v};});inputs['layout-'+axis].max=axis==='x'?'320':'160';}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),dot=document.createElementNS(ns,'circle');svg.setAttribute('viewBox','0 0 320 160');svg.setAttribute('class','management-layout');svg.setAttribute('role','img');svg.setAttribute('aria-label','布局预览；拖动只修改草稿，坐标可用键盘输入');dot.setAttribute('r','8');dot.setAttribute('class','management-layout-point');svg.appendChild(dot);form.appendChild(svg);
  let drag=null;
  function move(e){if(drag!==e.pointerId||!permitted()||busy||pending)return;const r=svg.getBoundingClientRect();if(!r.width||!r.height)return;draft.layout={x:Math.round(Math.max(0,Math.min(320,(e.clientX-r.left)*320/r.width))),y:Math.round(Math.max(0,Math.min(160,(e.clientY-r.top)*160/r.height)))};sync();}
  svg.addEventListener('pointerdown',e=>{if(!permitted()||busy||pending)return;drag=e.pointerId;svg.setPointerCapture?.(e.pointerId);move(e);});svg.addEventListener('pointermove',move);svg.addEventListener('pointerup',e=>{if(drag===e.pointerId){drag=null;svg.releasePointerCapture?.(e.pointerId);}});svg.addEventListener('pointercancel',()=>drag=null);
  const clearRelations=el('button',{type:'button',onClick:()=>{
    if(!canChange()||draft.relations===null)return;
    draft.relations=null;proposalIntent=null;sync();
    text(status,'仅撤销了当前草稿中的本项目关系；日期和其他配置保留。'+(conflict?'配置版本已变化，请先重新读取当前版本，再检查并保存。':'请点击“保存配置”提交。'));
  }},'撤销本项目关系');
  function syncActionPanels(){
    proposalControls.length=0;historyControls.length=0;
    proposalsPanel.replaceChildren(el('h4',{},'关系提议确认'),el('p',{className:'management-note'},
      '按相关项目确认会保存当前全部草稿，并将提议中的其他项目加入关联项目。现有日期、优先级和其他关系保留。仅依据当前配置投影，不代表原始清单已完整验证，也不会执行任务或改变读取权限。'));
    const proposals=Array.isArray(view?.proposals)?view.proposals:[];
    if(!proposals.length)proposalsPanel.appendChild(el('p',{},'当前没有可供明确确认的配置投影提议。'));
    for(const proposal of proposals){
      const ref=proposal?.proposal_ref,members=proposal?.project_ids;
      const valid=validProposalRef(ref)&&proposal.mapping==='related_to'&&proposal.evidence_scope==='configured_projection_only'
        &&Array.isArray(members)&&members.length>=2&&new Set(members).size===members.length&&members.includes(projectId)
        &&members.every(id=>typeof id==='string'&&view.project_options?.some(option=>option.id===id));
      const row=el('div',{className:'stack'},el('p',{},valid?`提议：${ref.proposal_id}`:'提议身份或目标当前不可确认。'));
      if(valid){
        row.appendChild(el('p',{},'相关项目：'+members.filter(id=>id!==projectId).map(id=>view.project_options.find(p=>p.id===id)?.name||id).join('、')));
        const accept=el('button',{type:'button',onClick:async()=>{
          if(!canChange()||conflict||refreshValidation().length)return;
          const relations=draft.relations??{belongs_to:null,depends_on:[],related_to:[],independent:null};
          draft.relations={...relations,related_to:[...new Set([...relations.related_to,...members.filter(id=>id!==projectId)])]};
          proposalIntent=copy(ref);sync();await submit('set');
        }},'按相关项目确认 · '+ref.proposal_id);
        proposalControls.push(accept);row.appendChild(accept);
      }else row.appendChild(el('p',{className:'management-note'},'缺少有效提议引用或可用目标，不能据此确认关系。请重新读取当前版本核对。'));
      proposalsPanel.appendChild(row);
    }
    if(proposalIntent)proposalsPanel.appendChild(el('p',{className:'management-note'},`待提交的明确确认：${proposalIntent.proposal_id}。草稿保留；重试或重新读取后保存仍会核对这份提议引用。`));
    historyPanel.replaceChildren(el('h4',{},'管理配置历史'),el('p',{className:'management-note'},
      '恢复历史只载入草稿，不回退当前版本。请检查后点击“保存配置”，以当前版本重新提交。'));
    const history=Array.isArray(view?.history_recent)?view.history_recent.slice(0,20):[];
    if(!history.length)historyPanel.appendChild(el('p',{},'尚无本项目的近期管理配置历史。'));
    for(const item of history){
      if(!Number.isSafeInteger(item?.revision)||item.revision<1)continue;
      const row=el('div',{className:'stack'},el('p',{},`版本 ${item.revision}`));
      if(item.after===null)row.appendChild(el('p',{},'历史未配置；该次提交撤回了全部管理配置。'));
      else if(item.after&&typeof item.after==='object'&&!Array.isArray(item.after)){
        const restore=el('button',{type:'button',onClick:()=>{
          if(!canChange())return;
          draft=localDraft(item.after);proposalIntent=null;sync();
          text(status,`版本 ${item.revision} 的配置已载入草稿；当前版本仍为 ${view.revision}。`+(conflict?'配置版本已变化，请先重新读取当前版本，再检查并保存。':'请检查后点击“保存配置”。'));
        }},`恢复为版本 ${item.revision} 的配置`);
        historyControls.push(restore);row.appendChild(restore);
      }else row.appendChild(el('p',{},'该历史配置不可恢复，请核对当前版本。'));
      if(validProposalRef(item.proposal_ref))row.appendChild(el('details',{},el('summary',{},'提议确认来源：'+item.proposal_ref.proposal_id),
        el('p',{},'确认仅关联当前配置投影；原提议仍保持待确认提议身份。'),
        el('p',{},'提议内容指纹：'+item.proposal_ref.proposal_hash),el('p',{},'投影来源指纹：'+item.proposal_ref.source_sha256),
        el('p',{},'登记版本指纹：'+item.proposal_ref.registry_sha256)));
      historyPanel.appendChild(row);
    }
  }
  const saveButton=el('button',{type:'button',onClick:()=>submit('set')},'保存配置'),retract=el('button',{type:'button',onClick:()=>submit('retract')},'撤回全部管理配置'),reset=el('button',{type:'button',onClick:()=>{if(busy||pending||requiresReload)return;draft=localDraft(view.overlay);proposalIntent=null;conflict=false;text(status,'草稿已还原为当前读取的配置。');sync();}},'还原草稿'),read=el('button',{type:'button',onClick:async()=>{if(busy||pending||typeof reload!=='function')return;busy=true;refresh();try{const fresh=await reload();if(!fresh?.available||!Number.isSafeInteger(fresh.revision)||fresh.revision<0||typeof fresh.config_hash!=='string'||! /^[a-fA-F0-9]{64}$/.test(fresh.config_hash)||!Array.isArray(fresh.project_options))throw new Error('MANAGEMENT_VIEW_UNAVAILABLE');view=copy(fresh);conflict=false;requiresReload=false;text(status,'已重新读取当前版本；草稿保留，请检查后再保存。');sync();}catch{text(status,'当前配置读取失败，草稿保留。');}finally{busy=false;refresh();}}},'重新读取当前版本');
  async function submit(operation){if(busy||!permitted()||conflict||requiresReload)return;if(operation==='set'&&!pending){const errors=refreshValidation();if(errors.length){text(status,'配置未保存：'+errors.map(item=>item.message).join(' '));return;}}let command;try{command=pending??buildManagementCommand(view,projectId,operation,operation==='set'?draft:null,requestId(),operation==='set'?proposalIntent:null);}catch{text(status,'配置无效，请检查计划日期、时区、里程碑、数值和关系项目。');return;}const originalView=copy(view),originalDraft=copy(draft);
    const focusInvoker=document.activeElement,restoreFocus=section.contains?.(focusInvoker)===true;
    let focusMoved=false;
    const trackFocus=event=>{if(event.target!==focusInvoker&&event.target!==document.body)focusMoved=true;};
    if(restoreFocus)document.addEventListener?.('focusin',trackFocus);
    let confirmedReceipt=null;busy=true;refresh();text(status,'正在保存管理配置…');try{const result=await save(copy(command));if(!result?.receipt||result.receipt.request_id!==command.request_id||!Number.isSafeInteger(result.receipt.revision)||!Number.isSafeInteger(result.receipt.committed_revision)||result.receipt.committed_revision<1||result.receipt.committed_revision>result.receipt.revision)throw Object.assign(new Error(),{outcome:'UNKNOWN'});confirmedReceipt=result.receipt;pending=null;proposalIntent=null;syncActionPanels();if(!result.view||result.view.revision!==result.receipt.revision||typeof result.view.config_hash!=='string'||! /^[a-fA-F0-9]{64}$/.test(result.view.config_hash)){requiresReload=true;text(status,`本次提交版本 ${result.receipt.committed_revision} 已保存；当前值读取失败，草稿保留。请显式重新读取当前版本后再操作。`);return;}view=copy(result.view);draft=localDraft(view.overlay);text(status,`已保存：当前版本 ${result.receipt.revision}，本次提交版本 ${result.receipt.committed_revision}${result.receipt.replayed?'（重试已确认）':''}。`);sync();try{await onSaved?.(result.view,result.receipt);}catch{text(status,`配置已保存（版本 ${result.receipt.revision}），页面刷新失败，请重新读取。`);}}catch(error){if(confirmedReceipt){pending=null;view=originalView;draft=originalDraft;requiresReload=true;sync();text(status,`本次提交版本 ${confirmedReceipt.committed_revision} 已保存；当前值读取失败，草稿保留。请显式重新读取当前版本后再操作。`);}else if(error?.code==='MANAGEMENT_RELATION_CONFLICT'){pending=null;text(status,'归属关系形成循环，配置未保存；草稿保留，请修改关系后再保存。');}else if(error?.code==='MANAGEMENT_BUSY'){pending=null;text(status,'其他配置保存正在进行；本次未保存，草稿保留，请稍后重试。');}else if(error?.status===409||error?.code==='MANAGEMENT_CONFLICT'){pending=null;conflict=true;text(status,'配置版本已变化，草稿保留。请重新读取当前版本，再检查并保存。');}else if(error?.outcome==='UNKNOWN'||error?.outcome!=='NOT_COMMITTED'&&!(error?.status>=400&&error?.status<500)){pending=copy(command);text(status,'提交结果未知，草稿保留。点击“重试确认提交”将使用同一请求确认结果。');}else{pending=null;if(error?.status===401||error?.status===403)owner=false;text(status,error?.status===401||error?.status===403?'所有者权限失效，草稿保留。请重新认证后操作。':error?.status===503?'配置服务暂不可用，草稿保留。请核对配置初始化与服务状态。':'配置未保存，草稿保留。请检查权限或稍后重试。');}}finally{
      busy=false;refresh();
      if(restoreFocus)document.removeEventListener?.('focusin',trackFocus);
      const active=document.activeElement;
      if(restoreFocus&&!focusMoved&&section.isConnected&&(!active||active===document.body||active===focusInvoker||active.isConnected===false)){
        if(focusInvoker.isConnected&&section.contains(focusInvoker)&&!focusInvoker.disabled)focusInvoker.focus();
        else{status.setAttribute('tabindex','-1');status.focus();}
      }
    }}
  function syncRolling(){rollingSummary.replaceChildren();if(!Object.hasOwn(draft,'rolling_plan'))return;const p=draft.rolling_plan;if(p===null){rollingSummary.appendChild(el('p',{},'滚动预估：未配置。'));return;}if(!rollingPlan(p)){rollingSummary.appendChild(el('p',{},'滚动预估格式需要核对；原配置保留，当前草稿不可保存。'));return;}const range=w=>w?`${w.earliest} 至 ${w.latest}`:'待评估';rollingSummary.appendChild(el('h4',{},'滚动预估（只读）'));rollingSummary.appendChild(el('p',{},`以下均为评估日期当时的预估与验收范围，不随来源刷新自动改写；不是截止日期，也不是实际完成或验收记录。计划时区：${draft.timezone??'未知'}。`));rollingSummary.appendChild(el('p',{},`评估日期：${p.as_of}；下次复核：${p.next_review}；可信度：${({unknown:'未知',low:'低',medium:'中',high:'高'})[p.confidence]}。预计完成：${range(p.completion_window)}。`));if(p.next_milestone)rollingSummary.appendChild(el('p',{},`下一里程碑：${p.next_milestone.label}；预估窗口：${range(p.next_milestone.window)}${p.next_milestone.condition?`；条件：${p.next_milestone.condition}`:''}。`));for(const a of p.acceptance_windows)rollingSummary.appendChild(el('p',{},`${a.kind==='human'?'本人验收':'自动验收'}预估：${a.label}；窗口：${range(a.window)}；范围：${a.scope}。`));if(p.conditions.length)rollingSummary.appendChild(el('ul',{},p.conditions.map(c=>el('li',{},c))));rollingSummary.appendChild(el('p',{},'修改上方日期、优先级或关系时会完整保留这份滚动预估；撤回全部管理配置会同时撤回预估。'));}
  function sync(){syncRolling();syncActionPanels();warnings.replaceChildren(...(view?.warnings??[]).map(w=>el('li',{},({PROJECT_DEPENDENCY_CYCLE:'项目依赖存在循环；已保存的配置保留，请核对依赖关系。',MANAGEMENT_ORPHAN_REFERENCE:'部分关系项目当前不在可编辑范围内；已保存的配置保留，请核对关系。'})[w]??'管理关系存在需要核对的提示。')));zone.value=draft.timezone??'';syncMilestones();for(const k of ['priority','start_date','end_date','order'])inputs[k].value=draft[k]??'';inputs.watch.value=draft.watch===null?'':String(draft.watch);mode.value=draft.relations===null?'unconfigured':'confirmed';inputs.independent.value=draft.relations?.independent==null?'':String(draft.relations.independent);for(const k of ['belongs_to','depends_on','related_to']){const n=inputs['rel-'+k];n.replaceChildren();if(k==='belongs_to')n.appendChild(el('option',{value:''},'未配置'));for(const p of view?.project_options??[]){if(p.id===projectId)continue;const selected=k==='belongs_to'?draft.relations?.[k]===p.id:draft.relations?.[k]?.includes(p.id);n.appendChild(el('option',{value:p.id,selected:Boolean(selected)},p.name||p.id));}if(k==='belongs_to')n.value=draft.relations?.belongs_to??'';}
    for(const a of ['x','y'])inputs['layout-'+a].value=draft.layout?.[a]??'';dot.setAttribute('cx',String(draft.layout?.x??160));dot.setAttribute('cy',String(draft.layout?.y??80));dot.setAttribute('data-configured',String(draft.layout!==null));text(revision,view?.available?`管理配置当前版本：${view.revision}；关系：${view.relation_state==='owner_confirmed'?'所有者已确认':'未配置'}`:view?.reason==='MANAGEMENT_NOT_INITIALIZED'?'管理配置尚未初始化，当前版本未知。':'管理配置读取失败，当前版本未知；项目来源事实仍可查看。');refresh();}
  function refresh(){const fieldErrors=refreshValidation();let valid=false;try{buildManagementCommand(view,projectId,'set',draft,'validation');valid=fieldErrors.length===0;}catch{}for(const n of proposalControls)n.disabled=!canChange()||conflict||!valid;for(const n of historyControls)n.disabled=!canChange();clearRelations.disabled=!canChange()||draft.relations===null;for(const n of milestoneControls)n.disabled=!canChange();addMilestone.disabled=!canChange()||draft.milestones===null||draft.milestones.length>=1000;for(const n of controls)n.disabled=!permitted()||busy||Boolean(pending)||(n===zone||n===milestoneMode)&&requiresReload||n===addMilestone&&(!canChange()||draft.milestones===null||draft.milestones.length>=1000)||(n===inputs.independent||n===inputs['rel-belongs_to']||n===inputs['rel-depends_on']||n===inputs['rel-related_to'])&&draft.relations===null;saveButton.disabled=!permitted()||busy||conflict||requiresReload||!pending&&!valid;saveButton.textContent=pending?'重试确认提交':'保存配置';retract.disabled=!permitted()||busy||Boolean(pending)||conflict||requiresReload||view.overlay===null;reset.disabled=busy||Boolean(pending)||requiresReload;read.disabled=busy||Boolean(pending)||typeof reload!=='function';svg.setAttribute('aria-disabled',String(!permitted()||busy||Boolean(pending)));}
  section.appendChild(el('h3',{},'Hub 管理配置'));section.appendChild(el('p',{className:'management-note'},'这些设置独立于项目来源事实、执行任务和关系建议。保存只更新 Hub 管理配置。'));section.appendChild(revision);section.appendChild(validationSummary);section.appendChild(form);section.appendChild(rollingSummary);section.appendChild(proposalsPanel);section.appendChild(historyPanel);section.appendChild(el('div',{className:'management-actions'},saveButton,clearRelations,retract,reset,read));section.appendChild(status);section.appendChild(warnings);sync();if(!owner)text(status,'所有者未认证，管理配置只读。');else if(!view?.available||!view?.editable)text(status,'管理配置不可编辑，请先完成配置初始化或核对项目权限。');section.setOwner=value=>{const wasOwner=owner;owner=value===true;refresh();if(!owner)text(status,'所有者未认证，管理配置只读；草稿保留。');else if(!wasOwner)text(status,requiresReload?'配置已保存但当前值读取失败；草稿保留，请显式重新读取。':pending?'提交结果未知；草稿保留，请用同一请求重试确认。':permitted()?'所有者已认证，草稿保留。':'管理配置不可编辑，请先完成配置初始化或核对项目权限。');};return section;
}

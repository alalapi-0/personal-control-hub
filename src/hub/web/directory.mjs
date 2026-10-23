import {el} from './common.js';
import {attention, freshness as sourceFreshness, validDefinitionSource, overviewExplanations, overviewPermitted, currentSourceAvailable, projectFacts} from './connection_view.mjs';
import {renderBusinessPlan} from './business_tasks.mjs';

const statuses={active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'来源未提供状态'};
const types={internal_control_plane:'管理',governance_program:'治理',code:'开发',creative:'创作',document:'文档',unknown:'类型未知'};
const knownKey=(labels,value)=>Object.hasOwn(labels,value)?value:'unknown';

// Search only identity and explicitly sourced definition fields. In particular,
// local paths and similar-sounding names never create aliases or relationships.
export function projectSearchMatch(project,query=''){
  const q=String(query).trim().toLocaleLowerCase();
  if(!q)return {matched:true,reasons:[]};
  const candidates=[
    {field:'project_id',label:'项目ID',value:project.project_id,source:'project_id'},
    {field:'official_name',label:'正式名称',value:project.name,source:'name'},
  ];
  const fields=project.definition?.fields??{};
  for(const [field,label] of [['official_name','正式名称'],['aliases','已确认别名'],['purpose','用途'],['outputs','产出']]){
    const entry=fields[field];
    if(entry?.state!=='known'||!validDefinitionSource(entry.source,field))continue;
    const values=Array.isArray(entry.value)?entry.value:[entry.value];
    for(const value of values)candidates.push({field,label,value,source:entry.source});
  }
  const reasons=[],seen=new Set();
  for(const candidate of candidates){
    if(typeof candidate.value!=='string'||!candidate.value.toLocaleLowerCase().includes(q))continue;
    const key=JSON.stringify([candidate.field,candidate.value]);
    if(!seen.has(key)){seen.add(key);reasons.push(candidate);}
  }
  return {matched:reasons.length>0,reasons};
}

// All views share this projection of the complete directory. Grouping never
// invents project relationships or changes the original directory order.
export function projectDirectory(projects,{query='',status='',freshness='',groupBy='none'}={}){
  const matches=new Map();
  const rows=projects.filter(project=>{
    const match=projectSearchMatch(project,query);
    const included=match.matched
    &&(!status||project.business?.normalized_status===status)
    &&(!freshness||(freshness==='attention'?attention(project):freshness==='fresh'?project.freshness?.state==='fresh':false));
    if(included)matches.set(project.project_id,match.reasons);
    return included;
  });
  const groups=[],byKey=new Map();
  for(const project of rows){
    let key='all',label='全部项目';
    if(groupBy==='status'){
      key=knownKey(statuses,project.business?.normalized_status);label=statuses[key];
    }else if(groupBy==='type'){
      key=knownKey(types,project.declared?.project_type);label=types[key];
    }else if(groupBy==='freshness'){
      key=sourceFreshness(project);label=key;
    }
    if(!byKey.has(key)){
      const group={key,label,rows:[]};byKey.set(key,group);groups.push(group);
    }
    byKey.get(key).rows.push(project);
  }
  return {rows,groups,total:projects.length,matched:rows.length,matches};
}

export function renderDirectoryTree(rows){
  const tree=el('div',{className:'stack directory-tree','aria-label':'项目来源业务树'});
  if(!rows.length)tree.append(el('p',{className:'empty'},'没有匹配的项目。'));
  for(const project of rows){
    const status=statuses[knownKey(statuses,project.business?.normalized_status)];
    tree.append(el('details',{className:'panel stack directory-project','data-project-id':project.project_id},
      el('summary',{},`${project.name??project.project_id} · ${status}`),
      el('a',{href:`#projects/${encodeURIComponent(project.project_id)}`},'进入项目详情'),
      renderBusinessPlan(project)));
  }
  return tree;
}

const explanationLabels={purpose:'目标与用途',outputs:'主要产出',current_work:'当前工作',next_action:'下一步',actor:'行动人'};
const objectLabels={Project:'项目',Goal:'目标',Phase:'阶段',Round:'轮次',Task:'任务',Artifact:'产物',FileRef:'文件引用',Workflow:'工作流',Run:'运行',Service:'服务',Tool:'工具',Decision:'决定',Review:'核验'};
const publicText=value=>{
  if(typeof value!=='string')return null;
  const withoutURLs=value.replace(/https?:\/\/[^\s<>"']+/gi,' ');
  return /(?<![\w./])\/[^\s<>"'`()\[\]{};,!?]+/.test(withoutURLs)?null:value;
};
const cleanText=value=>publicText(value)||'内容含私有位置或尚未提供，未在总览显示。';
const factDate=value=>typeof value==='string'&&/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value))?value:null;

export function overviewProject(project){
  const fields=overviewExplanations(project).map(v=>{
    const text=v.known?publicText(v.text):null;
    return {...v,known:v.known&&text!==null,text:text??'未知',state:v.known&&text!==null?'known':'unknown',
      reason:v.known&&text===null?'来源文字包含私有文件位置，未公开。':publicText(v.reason)||(!v.known?'当前字段尚无可公开的来源依据。':null)};
  });
  const permitted=overviewPermitted(project);
  const current=permitted&&currentSourceAvailable(project),facts=Object.fromEntries((permitted?projectFacts(project):[]).flatMap(g=>g.fields).map(v=>[v.key,v]));
  const get=key=>facts[key]?.known?cleanText(facts[key].text):'未知';
  const factAt=current&&project?.freshness?.fact_time_state==='known'?factDate(project.freshness.fact_updated_at):null;
  return {project_id:project.project_id,name:cleanText(project.name||project.project_id),fields,
    collection:sourceFreshness(project),business:get('current_work.status'),
    progress:`${get('progress.completed')} / ${get('progress.total')} · ${get('progress.counting_basis')}`,
    completionInferred:false,factAt,engineering:get('verification.status'),availability:'产物可用性未知 · 定义与引用不证明文件可用',
    attention:attention(project),readAllowed:permitted};
}

export function overviewActivity(projects){
  const rows=projects.map(overviewProject);
  return {recent:rows.filter(p=>p.factAt).sort((a,b)=>Date.parse(b.factAt)-Date.parse(a.factAt)),
    attention:rows.filter(p=>p.attention),unknownChangeTime:rows.filter(p=>!p.factAt).length};
}

function evidence(field){
  return el('details',{className:'overview-evidence'},el('summary',{},field.known?'来源依据':'未知原因'),
    el('p',{},field.known?'来自项目已核对字段。':'来源未提供可公开核对的'+field.label+'。'),
    field.reason?el('p',{className:'caption'},cleanText(field.reason)):null,
    field.source?el('pre',{},JSON.stringify(field.source,(key,value)=>typeof value==='string'?publicText(value):value,2)):null,
    field.interpretation?el('p',{className:'caption'},'Hub说明（非来源事实）：'+cleanText(field.interpretation)):null);
}

export function renderOverviewCard(project,{onSelect=null,reasons=[]}={}){
  const p=overviewProject(project),f=Object.fromEntries(p.fields.map(v=>[v.key,v]));
  const title=el('a',{href:'#projects/'+encodeURIComponent(p.project_id)},p.name);
  const card=el('article',{className:'project-row overview-card','data-project-id':p.project_id},
    el('h3',{},title),el('p',{className:'caption'},'业务：'+p.business+' · 来源：'+p.collection),
    el('dl',{className:'overview-card-work'},...['current_work','next_action'].map(k=>el('div',{},el('dt',{},f[k].label),el('dd',{},f[k].known?f[k].text:'未知 · 来源未提供')))),
    el('p',{className:'caption'},'行动人：'+(f.actor.known?f.actor.text:'未知')),
    el('details',{},el('summary',{},'说明与依据'),...p.fields.map(field=>el('div',{className:'overview-field'},el('strong',{},field.label),el('p',{},field.text),evidence(field))),
      el('p',{className:'caption'},'局部进度：'+p.progress+'；不据此推断项目完成。'),
      el('p',{className:'caption'},'工程检查：'+p.engineering),el('p',{className:'caption'},p.availability)),
    reasons.length?el('p',{className:'caption'},'匹配：'+reasons.map(r=>cleanText(r.label)).join('、')):null);
  if(onSelect)card.append(el('button',{type:'button',className:'subtle','aria-label':'聚焦'+p.name,onClick:()=>onSelect(p.project_id)},'聚焦'));
  return card;
}

export function renderOverviewFocus(project){
  const p=overviewProject(project),f=Object.fromEntries(p.fields.map(v=>[v.key,v]));
  return el('section',{className:'overview-focus','aria-label':'当前项目目标与下一步'},
    el('h2',{},el('a',{href:'#projects/'+encodeURIComponent(p.project_id)},p.name)),
    el('dl',{className:'overview-focus-work'},...['purpose','current_work','next_action'].map(k=>el('div',{},el('dt',{},f[k].label),el('dd',{},f[k].known?f[k].text:'未知 · 来源未提供')))),
    el('p',{className:'caption'},'行动人：'+(f.actor.known?f.actor.text:'未知')+' · 来源：'+p.collection),
    el('details',{},el('summary',{},'五项说明、局部进度与来源'),...p.fields.map(field=>el('div',{className:'overview-field'},el('strong',{},field.label),el('p',{},field.text),evidence(field))),
      el('p',{},'局部进度：'+p.progress+'；不代表整个项目完成。'),el('p',{},'工程检查：'+p.engineering),el('p',{},p.availability),
      el('p',{},p.factAt?'来源事实更新时间：'+p.factAt:'业务变化时间未知；最近读取时间不作为事实变化。')));
}

export function graphObjects(snapshot,projectId){
  const invalid=()=>{throw Object.assign(new Error('GRAPH_SNAPSHOT_INVALID'),{code:'GRAPH_SNAPSHOT_INVALID'});};
  const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),key=v=>JSON.stringify([v.id??v.object_id,v.revision]);
  if(snapshot?.schema_version!=='1.0'||snapshot.read_only!==true||snapshot.project_id!==projectId
    ||snapshot.compatibility?.major!==1||snapshot.compatibility?.minimum_reader!=='1.0'
    ||!hash(snapshot.source_version)||!hash(snapshot.projection_version)||!Array.isArray(snapshot.objects)
    ||snapshot.objects.length>10000||!Array.isArray(snapshot.rows)||snapshot.rows.length!==snapshot.objects.length)invalid();
  const byKey=new Map();
  for(const object of snapshot.objects){
    if(object?.project_id!==projectId||!Object.hasOwn(objectLabels,object.type)||typeof object.native_id!=='string'
      ||object.id!==`${projectId}/${object.type}/${object.native_id}`||!(object.revision===null||typeof object.revision==='string')
      ||!['fact','candidate'].includes(object.identity)||typeof object.label!=='string'||!object.source||byKey.has(key(object)))invalid();
    byKey.set(key(object),object);
  }
  const used=new Set();
  const entries=snapshot.rows.map(row=>{
    const k=key(row),object=byKey.get(k);
    if(!object||used.has(k)||row.type!==object.type||row.label!==object.label||row.identity!==object.identity
      ||row.source_version!==object.source.version||row.projection_version!==snapshot.projection_version)invalid();
    used.add(k);return {key:k,row,object};
  });
  return {projectId,sourceVersion:snapshot.source_version,projectionVersion:snapshot.projection_version,entries,unknownTypes:snapshot.unknown_types||{}};
}

export function createGraphReader(request,onChange=()=>{}){
  let generation=0,state={kind:'empty',projectId:null,snapshot:null};
  const publish=value=>{state=value;onChange(state);return state;};
  async function select(projectId,allowed=true){
    const token=++generation;
    if(!allowed)return publish({kind:'disabled',projectId,snapshot:null});
    publish({kind:'loading',projectId,snapshot:null});
    try{
      const snapshot=graphObjects(await request('/api/projects/'+encodeURIComponent(projectId)+'/graph'),projectId);
      if(token!==generation)return state;
      return publish({kind:snapshot.entries.length?'ready':'empty',projectId,snapshot});
    }catch(error){
      if(token!==generation)return state;
      return publish({kind:error?.status===404?'unavailable':error?.status===403?'disabled':'error',projectId,snapshot:null});
    }
  }
  return {select,retry:()=>state.projectId?select(state.projectId,state.kind!=='disabled'):Promise.resolve(state),
    clear(){generation++;state={kind:'empty',projectId:null,snapshot:null};},get state(){return state;}};
}

export function renderGraphObjects(state,{retry=()=>{}}={}){
  const panel=el('section',{className:'graph-objects stack','aria-label':'选中项目的图对象列表'});
  panel.append(el('h2',{},'项目对象'),el('p',{className:'caption'},'以下仅来自选中项目的一次图快照；与全目录卡片不是同一请求或冻结版本。此处是对象列表，尚非可编辑图画布。'));
  const labels={loading:'正在读取已存图对象…',empty:'当前没有已发布的对象。',disabled:'本项目对象读取未获允许；目录内容保留。',unavailable:'当前后台尚未提供图接口；目录内容保留。',error:'图对象读取失败；目录内容保留，未混用旧版本。'};
  if(state.kind!=='ready'){
    panel.append(el('p',{role:'status'},labels[state.kind]||labels.empty));
    if(['error','unavailable','empty'].includes(state.kind))panel.append(el('button',{type:'button',onClick:retry},'重试只读对象'));
    return panel;
  }
  panel.append(el('ul',{className:'graph-object-list'},...state.snapshot.entries.map(({key,row,object})=>el('li',{'data-object-key':key},
    el('strong',{},cleanText(row.label)),el('span',{className:'caption'},' · '+objectLabels[row.type]+' · '+(row.identity==='candidate'?'候选，尚非事实':'来源发布')),
    el('details',{},el('summary',{},'版本与来源'),el('p',{},'对象修订：'+cleanText(String(row.revision??'未知'))),
      el('p',{},'来源版本：'+cleanText(String(object.source.version??'未知'))),
      el('p',{},'适用性：'+({current:'当前',historical:'历史',unknown:'未知',unavailable:'不可用'}[object.source.applicability]||'未知')))))));
  const absent=Object.keys(state.snapshot.unknownTypes).filter(t=>Object.hasOwn(objectLabels,t)).map(t=>objectLabels[t]);
  if(absent.length)panel.append(el('p',{className:'caption'},'暂无真实稳定来源对象：'+absent.join('、')+'；不补造目标或运行身份。'));
  return panel;
}

import {el} from './common.js';
import {attention, freshness as sourceFreshness, validDefinitionSource} from './connection_view.mjs';
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

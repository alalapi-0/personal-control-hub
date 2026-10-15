import {el} from './common.js';
import {attention, freshness as sourceFreshness} from './connection_view.mjs';
import {renderBusinessPlan} from './business_tasks.mjs';

const statuses={active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'来源未提供状态'};
const types={internal_control_plane:'管理',governance_program:'治理',code:'开发',creative:'创作',document:'文档',unknown:'类型未知'};
const knownKey=(labels,value)=>Object.hasOwn(labels,value)?value:'unknown';

// All views share this projection of the complete directory. Grouping never
// invents project relationships or changes the original directory order.
export function projectDirectory(projects,{query='',status='',freshness='',groupBy='none'}={}){
  const q=String(query).trim().toLocaleLowerCase();
  const rows=projects.filter(project=>(!q||`${project.name??''} ${project.project_id??''}`.toLocaleLowerCase().includes(q))
    &&(!status||project.business?.normalized_status===status)
    &&(!freshness||(freshness==='attention'?attention(project):freshness==='fresh'?project.freshness?.state==='fresh':false)));
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
  return {rows,groups,total:projects.length,matched:rows.length};
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

import {el} from './common.js';
import {validDefinitionSource} from './connection_view.mjs';

const labels={outgoing:{belongs_to:'归属项目',depends_on:'依赖项目',related_to:'相关项目'},
  incoming:{belongs_to:'包含项目',depends_on:'被以下项目依赖',related_to:'被以下项目关联'}};
const states={unconfigured:'尚未配置本项目的管理关系；不能据此推断项目独立。',
  explicit_empty:'所有者明确登记本项目暂无管理关系。',unavailable:'管理关系当前不可读取。'};
const statuses={active:'进行中',blocked:'受阻',paused:'已暂停',complete:'已完成',unknown:'未知'};
const short=v=>typeof v==='string'?v.slice(0,12):'未知';

export function renderRelations(project,{names=new Map()}={}){
  const id=project.project_id,managed=project.management?.relation_view;
  const panel=el('section',{className:'panel stack','aria-label':'项目关系','data-live-project-relations':id},el('h2',{},'项目关系'));
  const link=(target,label,eligible=true)=>eligible?el('a',{href:'#projects/'+encodeURIComponent(target)},label||names.get(target)||target):el('span',{className:'warning'},'目标当前不在可读取范围');
  const confirmed=el('section',{className:'stack','aria-label':'已确认管理关系'},el('h3',{},'已确认管理关系'));
  confirmed.append(el('p',{className:'caption'},managed?.available?`管理版本 ${managed.revision} · 配置 ${short(managed.config_hash)}`:'管理版本未知'));
  if(!managed||managed.state!=='owner_confirmed')confirmed.append(el('p',{className:'muted'},states[managed?.state]||states.unavailable));
  if(managed?.independent!==null&&managed?.independent!==undefined)confirmed.append(el('p',{},managed.independent?'所有者已登记为独立项目。':'所有者已明确登记为非独立项目。'));
  for(const direction of ['outgoing','incoming']){
    const edges=managed?.[direction]||[];
    for(const kind of ['belongs_to','depends_on','related_to']){
      const rows=edges.filter(edge=>edge.relation===kind);
      if(rows.length)confirmed.append(el('div',{},el('h4',{},labels[direction][kind]),el('ul',{},...rows.map(edge=>el('li',{},link(edge.project_id,edge.name,edge.eligible))))));
    }
  }
  if(managed?.warnings?.includes('PROJECT_DEPENDENCY_CYCLE'))confirmed.append(el('p',{className:'warning'},'项目依赖形成循环；当前允许保留，但需要人工复核。'));
  if(managed?.warnings?.includes('MANAGEMENT_ORPHAN_REFERENCE'))confirmed.append(el('p',{className:'warning'},'部分已保存目标当前不可用；关系保留，需重新核对目标。'));
  panel.append(confirmed);

  const source=el('section',{className:'stack','aria-label':'来源声明关系'},el('h3',{},'来源声明与项目角色'));
  const fields=project.definition?.fields||{};
  for(const [key,title] of [['roles','项目角色'],['primary_project_id','所属主项目'],['working_copies','工作副本']]){
    const field=fields[key],known=field?.state==='known'&&validDefinitionSource(field.source,key);
    const values=known?(Array.isArray(field.value)?field.value:[field.value]):[];
    source.append(el('div',{},el('h4',{},title),values.length?el('ul',{},...values.map(value=>el('li',{},key==='roles'?String(value):link(value,names.get(value),names.has(value))))):el('p',{className:'muted'},field?.state==='empty'&&validDefinitionSource(field.source,key)?'来源明确未列出':'来源未提供当前可核对值'),
      known?el('details',{},el('summary',{},'查看声明依据'),el('p',{className:'caption'},'来源版本 '+short(field.source.sha256)),el('p',{className:'caption'},'观察时间 '+(field.source.observed_at||'未知'))):null));
  }
  const sourceParent=fields.primary_project_id,ownerParent=managed?.outgoing?.find(edge=>edge.relation==='belongs_to');
  if(sourceParent?.state==='known'&&validDefinitionSource(sourceParent.source,'primary_project_id')&&ownerParent&&sourceParent.value!==ownerParent.project_id)
    source.append(el('p',{className:'warning'},'来源主项目与管理归属不同；两项声明均保留，请核对各自依据。'));
  panel.append(source);

  const impact=managed?.dependency_impact;
  const effects=el('section',{className:'stack','aria-label':'依赖影响'},el('h3',{},'依赖影响（派生）'));
  effects.append(el('p',{className:impact?.status==='affected'?'warning':'caption'},impact?.status==='affected'?'依赖来源存在受阻项，需要复核排期。':impact?.status==='clear'?'当前可核对的已登记依赖未显示受阻。':'依赖影响尚无法完整核对。'));
  for(const path of impact?.paths||[]){
    const version=path.source_version||{};
    effects.append(el('article',{className:'stack'},el('p',{},'路径：'+path.path.map(target=>names.get(target)||target).join(' → ')),
      el('p',{},path.reason==='dependency_blocked'?'依赖项目来源状态为受阻。':'依赖项目当前状态或读取资格未知。'),
      el('p',{className:'caption'},`来源状态：${statuses[path.source_status]||'未知'} · 来源新鲜度：${path.source_freshness==='fresh'?'当前':'待核对'}`),
      el('details',{},el('summary',{},'查看影响依据'),el('p',{},`来源账本 ${version.ledger_head?.sequence??'未知'} · 结果 ${short(version.latest_result_hash)} · 登记 ${short(version.registry_sha256)}`))));
  }
  if(impact?.truncated)effects.append(el('p',{className:'warning'},'依赖范围超过本次展示上限；影响结论不完整。'));
  effects.append(el('p',{className:'caption'},'派生提示不改变项目来源状态或日期；关系不授予访问、执行或分发权限。'));
  panel.append(effects);
  return panel;
}

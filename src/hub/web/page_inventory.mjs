import {el} from './common.js';

const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const text=(v,max=500)=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
const id=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const object=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const stable=v=>JSON.stringify(v&&typeof v==='object'?Array.isArray(v)?v.map(x=>JSON.parse(stable(x))):Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(stable(v[k]))])):v);
const same=(a,b)=>stable(a)===stable(b);
const domains={implementation:'实施页面',design:'设计页面'};
const kinds={route:'路由',dialog:'对话框',tab:'标签页',screen:'屏幕',flow:'流程',document:'文档',other:'其他页面单元'};
const states={current:'与当前来源版本一致',historical:'历史版本',unknown_version:'版本未知'};
const selector=v=>object(v,['path'])&&Array.isArray(v.path)&&v.path.length>0&&v.path.length<=20&&v.path.every(x=>text(x,500)||(Number.isSafeInteger(x)&&x>=0))
  ||v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===2&&text(v.heading,500)&&(['label','table_label'].some(k=>text(v[k],500))||Number.isSafeInteger(v.ordered_item)&&v.ordered_item>0);
const timestamp=v=>v===null||typeof v==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(v)&&!Number.isNaN(Date.parse(v));
const artifact=v=>typeof v==='string'&&v.startsWith('data/previews/')&&v.length<=1000&&!/[\\%\x00-\x1f?#]/.test(v)&&v.split('/').every(x=>x&&x!=='.'&&x!=='..'&&/^[A-Za-z0-9_.-]+$/.test(x))&&/\.(png|jpg|jpeg|webp|gif)$/.test(v);
function displayable(value,projectId){
  if(!object(value,['schema_version','project_id','domains','previews'])||value.schema_version!=='1.0'||value.project_id!==projectId||!Array.isArray(value.domains)||value.domains.length!==2||new Set(value.domains.map(d=>d?.domain)).size!==2||!Array.isArray(value.previews)||value.previews.length>3)return false;
  const pages=new Map();
  for(const d of value.domains){
    if(!object(d,['domain','known_page_count','completeness','counting_basis','pages'])||!Object.hasOwn(domains,d.domain)||!['complete','partial','unknown'].includes(d.completeness)||!text(d.counting_basis,2000)||!Array.isArray(d.pages)||d.pages.length>10000||new Set(d.pages.map(p=>p?.id)).size!==d.pages.length)return false;
    if(d.completeness==='unknown'?d.known_page_count!==null||d.pages.length!==0:!Number.isSafeInteger(d.known_page_count)||d.known_page_count<0||d.known_page_count!==d.pages.length)return false;
    for(const p of d.pages){if(!object(p,['id','label','kind','source_sha256','build_sha256'])||!id(p.id)||!text(p.label)||!Object.hasOwn(kinds,p.kind)||p.source_sha256!==null&&!hash(p.source_sha256)||p.build_sha256!==null&&!hash(p.build_sha256))return false;pages.set(`${d.domain}:${p.id}`,p);}
  }
  if(new Set(value.previews.map(p=>p?.id)).size!==value.previews.length)return false;
  return value.previews.every(p=>object(p,['id','project_id','domain','page_id','artifact_ref','artifact_sha256','captured_at','source_sha256','build_sha256'])&&id(p.id)&&p.project_id===projectId&&pages.has(`${p.domain}:${p.page_id}`)&&artifact(p.artifact_ref)&&hash(p.artifact_sha256)&&timestamp(p.captured_at)&&(p.source_sha256===null||hash(p.source_sha256))&&(p.build_sha256===null||hash(p.build_sha256)));
}
function version(preview,page){
  if(preview.source_sha256!==null&&page.source_sha256!==null&&preview.source_sha256!==page.source_sha256||preview.build_sha256!==null&&page.build_sha256!==null&&preview.build_sha256!==page.build_sha256)return 'historical';
  if(preview.source_sha256===null||page.source_sha256===null||(preview.build_sha256===null)!==(page.build_sha256===null))return 'unknown_version';
  return 'current';
}

export function pageInventory(project){
  const unavailable={state:'unavailable',reason:'当前页面清单证据不可用。',value:null,previews:[],proof:null,observedAt:null};
  if(project?.freshness?.local_presence==='removed_local'||project?.freshness?.state==='removed_local'||project?.declared?.connection_read_allowed!==true)return {...unavailable,reason:'按当前读取权限，页面清单不可查看。'};
  const record=project?.operational?.latest_attempt;
  if(record?.success===false)return {...unavailable,state:'failed',reason:'最新来源读取失败；历史清单不能替代当前页面清单。'};
  if(project?.freshness?.state!=='fresh'||project?.freshness?.authority_drift===true)return {...unavailable,state:'stale',reason:'来源已过期或规则已变化，请重新核对来源；历史记录不代表当前清单。'};
  if(record?.schema_version!=='2.0'||record.success!==true||record.project_id!==project.project_id||!text(record.observed_at))return unavailable;
  const extension=record.extensions;
  if(!extension||['1.0','1.1','1.2'].includes(extension.schema_version))return {...unavailable,state:'not_declared',reason:'来源尚未声明页面清单；页面数量未知。'};
  if(extension.schema_version!=='1.3'||!extension.fact||typeof extension.fact!=='object'||Array.isArray(extension.fact))return unavailable;
  if(!Object.hasOwn(extension.fact,'page_inventory'))return {...unavailable,state:'not_declared',reason:'来源尚未声明页面清单；页面数量未知。'};
  const field=extension.fact.page_inventory,view=project?.business?.page_inventory;
  if(field?.value===null)return {...unavailable,state:'unknown',reason:'来源明确保留页面清单未知；未知不等于没有页面。'};
  const proof=field?.proof;
  if(!object(field,['value','unknown_reason','proof'])||field.unknown_reason!==null||!hash(proof?.sha256)||!id(proof.source_ref)||proof.observed_at!==record.observed_at||!selector(proof.selector)||!Array.isArray(record.sources)||record.sources.filter(s=>s.id===proof.source_ref&&s.sha256===proof.sha256&&(s.observed_at===undefined||s.observed_at===record.observed_at)).length!==1||!displayable(field.value,project.project_id))return unavailable;
  if(view?.available!==true||view.reason!==null||view.observed_at!==record.observed_at||!same(view.value,field.value)||!same(view.proof,proof)||!Array.isArray(view.previews)||view.previews.length!==field.value.previews.length)return unavailable;
  const pageMap=new Map(field.value.domains.flatMap(d=>d.pages.map(p=>[`${d.domain}:${p.id}`,p])));
  for(let i=0;i<view.previews.length;i++){const p=view.previews[i],source=field.value.previews[i];if(!object(p,[...Object.keys(source),'version_state','version_reason'])||!same(Object.fromEntries(Object.keys(source).map(k=>[k,p[k]])),source)||p.version_state!==version(source,pageMap.get(`${source.domain}:${source.page_id}`))||p.version_reason!==null&&!text(p.version_reason,2000))return unavailable;}
  return {state:'current',reason:null,value:structuredClone(view.value),previews:structuredClone(view.previews),proof:structuredClone(proof),observedAt:record.observed_at};
}

export function renderPageInventory(project){
  const view=pageInventory(project),section=el('section',{className:'panel stack','aria-label':'页面清单与代表预览'},el('h2',{},'页面清单与代表预览'));
  if(!view.value){section.append(el('p',{className:['failed','stale','unavailable'].includes(view.state)?'warning':'caption'},view.reason));return section;}
  section.append(el('p',{className:'caption'},'实施与设计分别计数，代表预览只是来源登记的图片元数据；此处未读取图片、核对图片内容或执行采集，也不代表本人验收。'));
  for(const d of view.value.domains){const count=d.completeness==='unknown'?'页面数量未知':d.completeness==='partial'?`已知子集 ${d.known_page_count} 个页面单元；全部总量未知`:`已声明完整清单：${d.known_page_count} 个页面单元`;
    section.append(el('section',{className:'stack'},el('h3',{},domains[d.domain]),el('p',{},count),el('p',{className:'caption'},'计数依据：'+d.counting_basis),d.completeness==='complete'&&d.known_page_count===0?el('p',{},'来源明确声明此域没有页面；代表预览不适用。'):null,...d.pages.map(p=>el('p',{},`${p.label} · ${kinds[p.kind]}；来源版本：${p.source_sha256===null?'未知':'已提供'}；构建版本：${p.build_sha256===null?'未知':'已提供'}`))));}
  section.append(el('h3',{},'代表预览引用'),el('p',{className:'caption'},`已登记 ${view.previews.length} 个代表预览引用；引用数量不是项目页面总数。`));
  for(const p of view.previews){const page=view.value.domains.find(d=>d.domain===p.domain).pages.find(x=>x.id===p.page_id);section.append(el('article',{className:'stack'},el('h4',{},page.label),el('p',{},`${domains[p.domain]} · ${states[p.version_state]}（仅元数据绑定）`),el('p',{},`实际采集时间：${p.captured_at??'未知'}；采集时来源版本：${p.source_sha256===null?'未知':'已提供'}；采集时构建版本：${p.build_sha256===null?'未知':'已提供'}。`),el('p',{className:'caption'},p.version_state==='current'?'图片字节与运行结果尚未核验。':p.version_state==='historical'?'历史版本不能作为当前页面证据。':'版本证据不足，不能认定为当前预览。')));}
  section.append(el('p',{className:'caption'},'来源观察时间：'+view.observedAt),el('details',{className:'diagnostics'},el('summary',{},'页面清单与预览诊断'),el('pre',{},JSON.stringify({proof:view.proof,inventory:view.value,previews:view.previews},null,2))));return section;
}

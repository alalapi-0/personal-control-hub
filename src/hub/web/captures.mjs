import {el} from './common.js';
import {pageInventory} from './page_inventory.mjs';

const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const id=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const timestamp=v=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(v)&&Number.isFinite(Date.parse(v));
const fields=['schema_version','request_id','request_sha256','project_id','page_id','domain','status','reason','method','captured_at','observed_at','source_sha256','build_sha256','source_proof_sha256','runtime_verified','artifact_ref','artifact_sha256','media_type','width','height','expires_at','provenance'];
const states={current:'当前版本',historical:'历史版本',unknown_version:'版本未知',expired:'已过期'};
function record(v,projectId,preview=false){
  if(!exact(v,preview?[...fields,'version_state','image_url']:fields)||v.schema_version!=='1.0'||!id(v.request_id)||!hash(v.request_sha256)||v.project_id!==projectId||!id(v.page_id)||v.domain!=='implementation'||!['success','failed'].includes(v.status)||!['web','existing_desktop'].includes(v.method)||!timestamp(v.observed_at)||!timestamp(v.expires_at)||v.captured_at!==null&&!timestamp(v.captured_at)||typeof v.runtime_verified!=='boolean'||!['source_sha256','build_sha256','source_proof_sha256'].every(k=>v[k]===null||hash(v[k])))return false;
  const p=v.provenance;
  if(!exact(p,['kind','report_sha256','reported_at','historical_candidate'])||p.kind!==(v.method==='web'?'web_capture':'existing_desktop')||![p.report_sha256,p.historical_candidate].every(x=>x===null||hash(x))||p.reported_at!==null&&!timestamp(p.reported_at))return false;
  if(v.status==='failed'){if(v.runtime_verified||typeof v.reason!=='string'||!/^[A-Z][A-Z0-9_]{1,79}$/.test(v.reason)||['artifact_ref','artifact_sha256','media_type','width','height'].some(k=>v[k]!==null))return false;}
  else if(v.reason!==null||!hash(v.artifact_sha256)||v.artifact_ref!==`data/previews/images/${projectId}/${v.artifact_sha256}.png`||v.media_type!=='image/png'||![v.width,v.height].every(x=>Number.isSafeInteger(x)&&x>=1&&x<=4096)||v.width*v.height>12000000||v.runtime_verified&&(v.method!=='web'||v.source_sha256===null))return false;
  return !preview||v.status==='success'&&Object.hasOwn(states,v.version_state)&&v.image_url===`/api/projects/${encodeURIComponent(projectId)}/captures/${encodeURIComponent(v.request_id)}/image`;
}
function currentRecord(project){const r=project?.operational?.latest_attempt;return project?.freshness?.state==='fresh'&&project?.freshness?.authority_drift!==true&&r?.schema_version==='2.0'&&r.success===true&&r.project_id===project.project_id?r:null;}
const selector=v=>exact(v,['path'])&&Array.isArray(v.path)&&v.path.length>0&&v.path.length<=20&&v.path.every(x=>typeof x==='string'&&x.length>0||Number.isSafeInteger(x)&&x>=0)||v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===2&&typeof v.heading==='string'&&v.heading.length>0&&(['label','boolean_label','table_label'].some(k=>typeof v[k]==='string'&&v[k].length>0)||Number.isSafeInteger(v.ordered_item)&&v.ordered_item>0);
function noUi(project){
  const r=currentRecord(project),e=r?.extensions,f=e?.fact?.has_ui,p=f?.proof;
  return ['1.0','1.1','1.2','1.3'].includes(e?.schema_version)&&f?.value===false&&f.unknown_reason===null&&hash(p?.sha256)&&id(p.source_ref)&&p.observed_at===r.observed_at&&timestamp(r.observed_at)&&selector(p.selector)&&Array.isArray(r.sources)&&r.sources.filter(s=>s.id===p.source_ref&&s.sha256===p.sha256&&(s.observed_at===undefined||s.observed_at===r.observed_at)).length===1;
}
function conservativeState(p,page,inventory,project,latest,now){
  if(p.method==='existing_desktop'||p.provenance.historical_candidate!==null)return 'historical';
  if(latest?.page_id===p.page_id&&latest.status==='failed')return 'historical';
  if(page&&(p.source_sha256!==null&&page.source_sha256!==null&&p.source_sha256!==page.source_sha256||p.build_sha256!==null&&page.build_sha256!==null&&p.build_sha256!==page.build_sha256))return 'historical';
  if(Date.parse(p.expires_at)<=now)return 'expired';
  if(p.version_state!=='current')return p.version_state;
  if(!currentRecord(project)||!page||!p.runtime_verified||p.captured_at===null||p.source_sha256===null||page.source_sha256===null||p.source_sha256!==page.source_sha256||(p.build_sha256===null)!==(page.build_sha256===null)||p.source_proof_sha256===null)return 'unknown_version';
  return 'current';
}
export function captureView(project,catalog,{now=Date.now()}={}){
  const unavailable={classification:'unavailable',reason:'实际页面预览暂不可读取；没有预览不代表没有界面。',previews:[],latestAttempt:null};
  const projectId=project?.project_id;
  if(!id(projectId)||project?.declared?.connection_read_allowed!==true||project?.freshness?.local_presence==='removed_local'||project?.freshness?.state==='removed_local')return {...unavailable,reason:'按当前读取权限，实际页面预览不可查看。'};
  if(!exact(catalog,['project_id','classification','reason','previews','latest_attempt','capture_performed','execution_allowed'])||catalog.project_id!==projectId||catalog.capture_performed!==false||catalog.execution_allowed!==false||!['available','not_applicable','not_implemented','unavailable'].includes(catalog.classification)||catalog.reason!==null&&typeof catalog.reason!=='string'||!Array.isArray(catalog.previews)||catalog.previews.length>3||new Set(catalog.previews.map(p=>p?.page_id)).size!==catalog.previews.length||new Set(catalog.previews.map(p=>p?.request_id)).size!==catalog.previews.length||!catalog.previews.every(p=>record(p,projectId,true))||catalog.latest_attempt!==null&&!record(catalog.latest_attempt,projectId)||!Number.isFinite(now))return unavailable;
  const inventory=pageInventory(project),domain=inventory.value?.domains.find(d=>d.domain==='implementation');
  if(catalog.classification==='not_applicable')return noUi(project)&&!catalog.previews.length?{...unavailable,classification:'not_applicable',reason:'当前来源明确声明没有界面；页面预览不适用。'}:unavailable;
  if(catalog.classification==='not_implemented')return domain?.completeness==='complete'&&domain.known_page_count===0&&!catalog.previews.length?{...unavailable,classification:'not_implemented',reason:'实施页面完整清单明确为零；尚无已登记实施页面，不代表项目没有界面需求。'}:unavailable;
  const previews=catalog.previews.map(p=>({...structuredClone(p),version_state:conservativeState(p,domain?.pages.find(x=>x.id===p.page_id),inventory,project,catalog.latest_attempt,now),label:domain?.pages.find(x=>x.id===p.page_id)?.label??'已保存页面截图'}));
  return {classification:catalog.classification,reason:previews.length?null:catalog.latest_attempt?.status==='failed'?'最近采集失败；原有成功截图保留，失败不代表页面不存在。':'尚无已保存的实际页面截图；页面是否存在仍以来源事实为准。',previews,latestAttempt:structuredClone(catalog.latest_attempt)};
}
export function renderCaptures(project,catalog){
  const view=captureView(project,catalog),section=el('section',{className:'panel stack','aria-label':'实际页面预览'},el('h2',{},'实际页面预览'));
  section.append(el('p',{className:'caption'},'这里只读取已保存截图；不会采集页面、启动项目、Agent 或模型。截图与当前版本核对不代表本人验收。'));
  if(view.reason)section.append(el('p',{className:'caption'},view.reason));
  if(view.latestAttempt?.status==='failed'&&view.previews.length)section.append(el('p',{className:'warning'},'最近采集失败；以下保留成功截图，并分别标明当前性。'));
  for(const p of view.previews){
    const version=el('p',{className:p.version_state==='current'?'caption':'warning'},states[p.version_state]+(p.version_state==='current'?' · 运行版本已核对，非本人验收':''));
    const image=el('img',{src:p.image_url,alt:p.label+'的已保存截图',loading:'lazy',decoding:'async',referrerPolicy:'no-referrer',width:p.width,height:p.height});
    const stage=el('div',{className:'image-stage'},image);
    const status=el('p',{role:'status','aria-live':'polite'},'正在读取已保存截图…');let failed=false;
    image.addEventListener('load',()=>{if(!failed)status.textContent='已保存截图已读取；图像不提供页面操作，也不代表本人验收。';});
    image.addEventListener('error',()=>{
      failed=true;image.removeAttribute('src');image.setAttribute('hidden','');stage.setAttribute('hidden','');version.className='warning';
      version.textContent='截图内容不可读取；版本状态无法核实。';
      status.textContent='请重新核对来源与截图记录；保留采集时间和来源说明，不会自动重试失效图像。';
    });
    section.append(el('article',{className:'stack'},el('h3',{},p.label),version,
      el('p',{},'实际采集时间：'+(p.captured_at??'未知')),
      p.provenance.reported_at?el('p',{className:'caption'},'历史报告时间：'+p.provenance.reported_at+'；报告时间不替代实际采集时间。'):null,
      p.method==='existing_desktop'?el('p',{className:'caption'},'已保存桌面截图；未证明当前运行版本。'):null,
      stage,status,
      el('details',{className:'diagnostics'},el('summary',{},'截图版本与来源诊断'),el('pre',{},JSON.stringify(p,null,2)))));
  }
  if(view.latestAttempt)section.append(el('details',{className:'diagnostics'},el('summary',{},'最近采集记录诊断'),el('pre',{},JSON.stringify(view.latestAttempt,null,2))));return section;
}

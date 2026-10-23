import {el,button,api} from './common.js';

export function materialUrl(row){
  if(!/^material-[a-f0-9]{32}$/.test(row?.id||'')||! /^[a-f0-9]{64}$/.test(row?.version||''))return null;
  return `/api/materials/${row.id}?version=${row.version}`;
}
export function reviewLink(reference){
  try{const u=new URL(reference);return u.protocol==='https:'&&u.hostname==='www.figma.com'&&!u.username&&!u.password&&!u.port?u.href:null;}catch{return null;}
}
export function materialVersionQuery(query){
  if([...query.keys()].some(key=>key!=='version')||query.getAll('version').length>1
      ||query.has('version')&&!/^[a-f0-9]{64}$/.test(query.get('version')))
    throw Object.assign(new Error('INVALID_QUERY'),{code:'INVALID_QUERY',status:400});
  return query.get('version');
}
export function materialLink(row){return materialUrl(row)?'#materials/'+encodeURIComponent(row.id)+'?version='+row.version:null;}
// Exact version selection is shared by the directory and detail reader. Names
// group comparisons only; they never select bytes or merge registered identity.
const readers=new WeakMap();
const kindNames={text:'文本',html_source:'HTML 源码',image:'图片',video:'短视频',review_reference:'专业审核引用'};
function versionSummary(row){
  const readable=row.content_readable===true||(row.content_readable===undefined&&row.available===true&&row.kind!=='review_reference');
  return el('div',{className:'stack'},
    el('p',{},`${row.project_name??'项目名称未知'} · ${kindNames[row.kind]??'类型未知'} · ${row.historical?'历史登记（非当前决策）':'当前登记（不代表已读）'}`),
    el('p',{},`用途：${row.purpose??'未知；原记录未明确声明'}`),
    el('p',{},`候选修订：${row.candidate_revision} · ${row.classification??'分类未知'}`),
    el('p',{className:readable?'caption':'warning'},row.kind==='review_reference'?'仅审核外链，不是本地内容读取':readable?'内容入口已核对；尚不代表本次已读':'内容读取未开放或版本未预核对'),
    el('details',{},el('summary',{},'查看版本与出处'),
      el('p',{},`原产物身份：${row.artifact_id} · 方案：${row.candidate_id}`),
      el('p',{},`内容版本：${row.version} · 内容 SHA256：${row.sha256??'未提供'}`),
      el('p',{},`登记观察版本：${row.observation_version??'未知'} · 核对时间：${row.observed_at??'未知'}（不是内容变更时间）`),
      el('p',{},`出处方法：${row.provenance?.method??'未知'} · 出处引用：${row.provenance?.source_refs?.join('、')||'未提供可公开引用'}`)));
}
export async function renderMaterials(target,{materialId=null,isCurrent=()=>true,expectedVersion=null}={}){
  if(!isCurrent())return;
  readers.get(target)?.dispose();
  const controller=new AbortController(),cleanups=[];
  const reader={dispose(){controller.abort();for(const cleanup of cleanups)cleanup();cleanups.length=0;}};
  readers.set(target,reader);
  const current=()=>readers.get(target)===reader&&!controller.signal.aborted&&isCurrent();
  const notice=el('p',{role:'status',className:'warning'});
  const section=el('section',{className:'stack'},el('h1',{},'授权产物'),
    el('p',{className:'muted'},'按原身份和精确内容版本读取；同名对照仅比较登记元数据。此视图没有执行权限。'),notice);
  target.replaceChildren(section);
  const reload=button('重新核对产物',async()=>{await renderMaterials(target,{materialId,isCurrent,expectedVersion});if(isCurrent())target.querySelector('button')?.focus();});
  section.append(reload,el('p',{role:'status'},'正在读取产物记录…'));
  let data;
  try{data=await api('/api/materials');}
  catch(error){if(!current())return;section.lastChild.remove();notice.textContent=error.code==='OWNER_AUTH_REQUIRED'||error.code==='OWNER_AUTH_UNAVAILABLE'?'连接所有者后可查看已授权产物。':error.code==='MATERIAL_VIEW_UNAVAILABLE'||error.code==='NOT_FOUND'?'当前后台尚未开放产物查看。':'产物暂不可读取；重连后请重新核对。';return;}
  if(!current())return;section.lastChild.remove();
  if(!Array.isArray(data?.materials)||data.materials.some(r=>!materialUrl(r))||new Set(data.materials.map(r=>JSON.stringify([r.id,r.version]))).size!==data.materials.length){notice.textContent='产物版本记录异常；未读取任何内容，请重新核对。';return;}
  section.append(el('p',{className:'caption'},`登记观察修订：${data.store_revision} · ${data.classification==='synthetic_fixture'?'隔离样本':'登记记录'} · 内容缓存关闭；登记更新不等于内容变化`));
  if(!data.materials.length){section.append(el('p',{className:'muted'},'当前没有可展示的登记产物。'));return;}
  function choice(row){return el('article',{className:'panel stack'},el('h2',{},row.display_name??row.artifact_id),versionSummary(row),el('a',{className:'project-row',href:materialLink(row)},'选择此精确版本'));}
  if(!materialId){
    const groups=new Map();
    for(const row of data.materials){const name=row.display_name??row.artifact_id;if(!groups.has(name))groups.set(name,[]);groups.get(name).push(row);}
    for(const [name,rows]of groups){
      const group=el('section',{className:'stack','aria-label':`${name} 版本对照`},el('h2',{},name));
      if(rows.length>1)group.append(el('p',{className:'caption'},`同名 ${rows.length} 个登记版本，身份分别保留；以下是元数据对照，不代表内容已读或相同。`));
      for(const row of rows)group.append(choice(row));section.append(group);
    }
    return;
  }
  const matches=data.materials.filter(m=>m.id===materialId),row=matches.find(m=>m.version===expectedVersion),url=materialUrl(row);
  section.append(el('a',{href:'#materials'},'← 所有产物'));
  if(!row||!url){
    notice.textContent=expectedVersion?'固定产物版本不存在或已变化；没有读取或自动切换到其他版本。':'此链接未指定精确内容版本，请明确选择后读取。';
    for(const alternative of matches)section.append(choice(alternative));return;
  }
  section.append(el('h2',{},row.display_name??row.artifact_id),versionSummary(row),
    el('a',{href:'#designs/'+encodeURIComponent(row.project_id)+'/'+encodeURIComponent(row.candidate_id)},'查看项目审核记录（不恢复历史为当前）'),
    el('a',{href:'#projects/'+encodeURIComponent(row.project_id)+'/sources'},'返回项目来源与产物'));
  if(row.kind==='review_reference'){
    notice.textContent='这是审核外链；未读取本地内容，也未验证外部页面。';
    const link=reviewLink(row.review_reference);
    if(link)section.append(el('a',{href:link,target:'_blank',rel:'noopener noreferrer'},'打开专业审核页'));
    else section.append(el('p',{},'审核引用尚无安全可用入口。'));
    return;
  }
  if(!row.available||row.content_readable===false){notice.textContent='这个精确版本尚未开放内容读取。';return;}
  const media=el('div',{className:'panel stack'});section.append(media);
  if(row.kind==='image'||row.kind==='video'){
    const v=row.kind==='image'?el('img',{src:url,alt:row.display_name??row.artifact_id,className:'preview'}):el('video',{src:url,controls:true,preload:'metadata',className:'preview'});
    const state=el('p',{role:'status'},'正在读取此版本内容…');media.append(v,state);
    const event=row.kind==='image'?'load':'loadedmetadata';
    const loaded=()=>{if(current())state.textContent=row.kind==='video'?'此版本视频元数据已读取；播放内容由播放器按范围继续读取。':'此版本图片已读取。';};
    const failed=()=>{if(!current())return;v.removeAttribute('src');state.textContent='此版本内容读取失败或已变化，没有切换到新版；请重新核对。';};
    v.addEventListener(event,loaded);v.addEventListener('error',failed);
    cleanups.push(()=>{v.removeEventListener(event,loaded);v.removeEventListener('error',failed);v.removeAttribute('src');});return;
  }
  const loading=el('p',{role:'status'},'正在读取此版本内容…');media.append(loading);
  try{
    const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if(!response.ok||response.headers.get('Content-Type')!=='text/plain; charset=utf-8')throw new Error('UNAVAILABLE');
    const text=await response.text();if(!current())return;
    if(new TextEncoder().encode(text).length>1024*1024)throw new Error('TOO_LARGE');
    media.replaceChildren(el('p',{role:'status',className:'caption'},row.kind==='html_source'?'此版本已读取；HTML 以源码显示。':'此版本 UTF-8 文本已读取。'),el('pre',{},text));
  }catch{if(current())media.replaceChildren(el('p',{role:'status',className:'warning'},'此版本内容不可读取或已变化，没有切换到新版；请重新核对。'));}
}

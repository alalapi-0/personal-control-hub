import {el,button,api} from './common.js';

export function materialUrl(row){
  if(!/^material-[a-f0-9]{32}$/.test(row?.id||'')||! /^[a-f0-9]{64}$/.test(row?.version||''))return null;
  return `/api/materials/${row.id}?version=${row.version}`;
}
export function reviewLink(reference){
  try{const u=new URL(reference);return u.protocol==='https:'&&u.hostname==='www.figma.com'&&!u.username&&!u.password&&!u.port?u.href:null;}catch{return null;}
}
export async function renderMaterials(target,{materialId=null,isCurrent=()=>true}={}){
  const notice=el('p',{role:'status',className:'warning'});
  const section=el('section',{className:'stack'},el('h1',{},'授权产物'),
    el('p',{className:'muted'},'登记内容与审核记录按版本读取。此视图没有执行权限。'),notice);
  target.replaceChildren(section);
  const reload=button('重新核对产物',async()=>{await renderMaterials(target,{materialId,isCurrent});if(isCurrent())target.querySelector('button')?.focus();});
  section.append(reload,el('p',{role:'status'},'正在读取产物记录…'));
  let data;
  try{data=await api('/api/materials');}
  catch(error){if(!isCurrent())return;section.lastChild.remove();notice.textContent=error.code==='OWNER_AUTH_REQUIRED'||error.code==='OWNER_AUTH_UNAVAILABLE'?'连接所有者后可查看已授权产物。':error.code==='MATERIAL_VIEW_UNAVAILABLE'||error.code==='NOT_FOUND'?'当前后台尚未开放产物查看。':'产物暂不可读取；重连后请重新核对。';return;}
  if(!isCurrent())return;section.lastChild.remove();
  section.append(el('p',{className:'caption'},`审核记录版本：${data.store_revision} · ${data.classification==='synthetic_fixture'?'隔离样本':'登记记录'} · 内容缓存关闭`));
  if(!data.materials.length){section.append(el('p',{className:'muted'},'当前没有可展示的登记产物。'));return;}
  if(!materialId){
    for(const row of data.materials)section.append(el('a',{className:'project-row',href:'#materials/'+encodeURIComponent(row.id)},
      el('h2',{},row.artifact_id),el('p',{},`${row.project_name} · ${{text:'文本',html_source:'HTML 源码',image:'图片',video:'短视频',review_reference:'专业审核引用'}[row.kind]||'类型未知'}`),
      el('p',{className:row.available?'caption':'warning'},row.available?'已核对登记版本':'尚未开放内容读取')));
    return;
  }
  const row=data.materials.find(m=>m.id===materialId),url=materialUrl(row);
  section.append(el('a',{href:'#materials'},'← 所有产物'));
  if(!row||!url){notice.textContent='产物记录已变化或不可用，请重新核对。';return;}
  section.append(el('h2',{},row.artifact_id),el('p',{},row.project_name),
    el('p',{className:'caption'},`方案 ${row.candidate_id} · 修订 ${row.candidate_revision}`),
    el('a',{href:'#designs/'+encodeURIComponent(row.project_id)+'/'+encodeURIComponent(row.candidate_id)},'查看原审核记录'));
  if(!row.available){notice.textContent='这个版本尚未开放内容读取。';return;}
  if(row.kind==='review_reference'){
    section.append(el('pre',{},row.review_reference));
    const link=reviewLink(row.review_reference);
    if(link)section.append(el('a',{href:link,target:'_blank',rel:'noopener noreferrer'},'打开专业审核页'));
    return;
  }
  const media=el('div',{className:'panel stack'});section.append(media);
  if(row.kind==='image'||row.kind==='video'){
    const v=row.kind==='image'?el('img',{src:url,alt:row.artifact_id,className:'preview'}):el('video',{src:url,controls:true,preload:'metadata',className:'preview'});
    const state=el('p',{role:'status'},'正在读取内容…');media.append(v,state);
    v.addEventListener(row.kind==='image'?'load':'loadedmetadata',()=>{state.textContent=row.kind==='video'?'可播放；暂停和定位使用播放器控件。':'图片已读取。';});
    v.addEventListener('error',()=>{v.removeAttribute('src');state.textContent='内容不可播放或登记版本已变化。请重新核对，或查看原审核记录。';});
    return;
  }
  const loading=el('p',{role:'status'},'正在读取内容…');media.append(loading);
  try{
    const response=await fetch(url,{credentials:'same-origin',cache:'no-store'});
    if(!response.ok||response.headers.get('Content-Type')!=='text/plain; charset=utf-8')throw new Error('UNAVAILABLE');
    const text=await response.text();if(!isCurrent())return;
    media.replaceChildren(el('p',{className:'caption'},row.kind==='html_source'?'HTML 以源码显示。':'UTF-8 文本'),el('pre',{},text));
  }catch{if(isCurrent())media.replaceChildren(el('p',{role:'status',className:'warning'},'内容不可读取或登记版本已变化，请重新核对。'));}
}

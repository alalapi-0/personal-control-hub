import {el,button,api} from './common.js';

export function validLivePreview(d){
  if(!d||d.source!=='real_code_readonly_fixture'||d.project_id!=='computer-study-plan'||d.execution_allowed!==false||d.business_data!=='fixture'||d.source_mapping!==null)return false;
  if(!/^[a-f0-9]{40}$/.test(d.code_head)||!/^[a-f0-9]{64}$/.test(d.manifest_hash)||typeof d.manifest_observed_at!=='string')return false;
  if(d.available===false)return d.origin===null&&d.frame_url===null;
  let u;try{u=new URL(d.origin);}catch{return false;}
  return d.available===true&&u.protocol==='http:'&&u.hostname==='127.0.0.1'&&u.port&&u.origin===d.origin&&d.frame_url===d.origin+'/progress.html';
}

// Live browsing and screenshot coordinates never share an annotation surface.
export function mountLivePreview(target,previewId,{isOwner,isCurrent=()=>true}={}){
  const status=el('p',{role:'status',className:'caption'},'真实代码预览尚未打开；区域批注使用下方固定截图。');
  let frame=null,mode='browse',generation=0,timer=null;
  const open=button('打开真实页面预览',async()=>{
    const g=++generation;open.disabled=true;status.textContent='正在核对只读来源与版本…';
    try{
      if(!isOwner()||mode!=='browse')return;
      const d=await api('/api/live-preview/'+encodeURIComponent(previewId));
      if(g!==generation||!target.isConnected||!isCurrent()||mode!=='browse')return;
      if(!validLivePreview(d)||d.available!==true){status.textContent='只读页面未运行；固定截图仍可查看并批注。';return;}
      frame?.remove();frame=el('iframe',{title:'真实代码页面 · 只读夹具数据',src:d.frame_url,
        sandbox:'allow-scripts allow-same-origin',referrerPolicy:'no-referrer',className:'controlled-preview'});
      let loaded=false;
      frame.addEventListener('load',()=>{loaded=true;clearTimeout(timer);status.textContent='预览框架已载入 · 业务数据为只读夹具；若页面无法显示，请关闭并使用固定截图。未提供元素或源码映射。';});
      frame.addEventListener('error',()=>{close();status.textContent='页面载入失败；请使用固定截图。';});
      target.append(frame);timer=setTimeout(()=>{if(!loaded){close();status.textContent='页面未响应；请使用固定截图。';}},5000);
    }catch{close();status.textContent='页面来源已失效或未授权；请重新核对版本。历史意见保留。';}
    finally{if(g===generation)open.disabled=!isOwner()||mode!=='browse';}
  });
  function close(){++generation;clearTimeout(timer);frame?.remove();frame=null;}
  const collapse=button('关闭页面，使用截图',()=>{close();status.textContent='页面已关闭；可对固定截图填写整版或区域意见。';});
  target.append(el('h3',{},'真实页面浏览'),el('p',{className:'warning'},'真实项目代码 · 只读开发预览 · 业务回答为夹具；不是生产学习活动。'),open,collapse,status);
  return {setMode(next){mode=next;if(next==='annotate'){close();status.textContent='区域批注中 · 真实页面已关闭，底层按钮不会接收点击或键盘。';}open.disabled=!isOwner()||mode!=='browse';},close};
}

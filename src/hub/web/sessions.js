import {el,button,api} from './common.js';

export const sourceLabels={cli:'CLI 来源',appServer:'App Server 来源',vscode:'IDE / 桌面来源'};
export function sessionState(row){
  return ({busy:'正在执行 · 未取得运行控制权',idle_unverified:'报告空闲 · 继续权限待核对',
    notLoaded:'未加载 · 不能据此判断空闲',error:'会话状态异常',unknown:'运行状态未知'})[row?.state]||'运行状态未知';
}
export function sourceState(group){
  return ({supported_empty:'本次筛选无匹配会话',readonly:'可读取元数据',error:'此次读取失败',
    unsupported:'当前接口不支持',unprobed:'尚未探测',stale:'记录已过期'})[group?.state]||'来源能力未知';
}

export async function renderSessions(target,{isCurrent=()=>true}={}){
  const root=el('section',{className:'stack session-view'},el('h1',{},'既有 Codex 会话'),
    el('a',{href:'#tasks'},'← 任务与结果'),
    el('p',{className:'muted'},'核对当前 Hub 项目的会话来源与准确 ID。这里只读取元数据；继续会话需要另行确认项目授权和运行归属。'));
  const content=el('div',{className:'stack','aria-live':'polite'});
  const refresh=button('重新核对会话',()=>load(true));
  root.append(refresh,content);target.replaceChildren(root);
  let live=true,hadRows=false,expiry=null;
  function clearOwner(event){
    if(event.detail?.owner_authenticated===true)return;
    live=false;clearTimeout(expiry);content.replaceChildren(el('p',{role:'status'},hadRows?'所有者连接已失效；会话信息已隐藏。':'连接所有者后可读取会话信息。'));
    refresh.disabled=false;
  }
  // Attach to this surface's lifetime; routing removes its private rows immediately.
  const controller=new AbortController();
  window.addEventListener('hub:identity',clearOwner,{signal:controller.signal});
  const observer=new MutationObserver(()=>{if(!root.isConnected){clearTimeout(expiry);controller.abort();observer.disconnect();}});
  observer.observe(target,{childList:true});
  async function load(focus=false){
    live=true;clearTimeout(expiry);refresh.disabled=true;content.setAttribute('aria-busy','true');
    content.replaceChildren(el('p',{role:'status'},'正在核对会话来源…'));
    try{
      const data=await api('/api/sessions');
      if(!isCurrent()||!live)return;
      hadRows=true;
      content.replaceChildren(el('h2',{},data.project),
        el('p',{className:'caption'},data.classification==='native_metadata'?'Linux 后端实际读取 · 每类最多三项':'隔离样本 · 不代表真实会话可用'),
        el('p',{className:'warning'},'所有继续操作保持关闭；“未加载”和“报告空闲”都不足以确认可安全继续。'));
      for(const group of data.sources||[]){
        const panel=el('section',{className:'panel stack'},el('h3',{},sourceLabels[group.source]||'来源待核对'),
          el('p',{},sourceState(group)));
        if(group.has_cursor)panel.append(el('p',{className:'caption'},'本次仅读取最多三项；翻页结果未读取，不能用于绑定。'));
        if(group.state==='error')panel.append(el('p',{className:'caption'},'保留读取失败；可重新核对，尚未发送任务。'));
        for(const row of group.rows||[]){
          panel.append(el('article',{className:'panel stack'},el('h4',{},'既有会话'),el('p',{},sessionState(row)),
            el('p',{className:'caption'},'准确会话 ID'),el('code',{},row.id),
            el('button',{type:'button',disabled:true},'继续会话 · 尚未授权')));
        }
        content.append(panel);
      }
      content.append(el('p',{className:'caption'},'云端与远程来源尚未探测；无匹配记录不表示该来源不可用。'),
        el('details',{},el('summary',{},'读取版本'),el('p',{},data.version),el('p',{},'本次观察时间：'+new Date(data.observed_at*1000).toLocaleString())));
      const remaining=Number(data.ttl_seconds)-Number(data.age_seconds);
      expiry=setTimeout(()=>{if(isCurrent()&&live)content.replaceChildren(el('p',{role:'status'},'会话记录已过期；重新核对后查看准确 ID。'));
      },Number.isFinite(remaining)?Math.max(0,remaining)*1000:0);
    }catch(error){
      if(!isCurrent()||!live)return;
      content.replaceChildren(el('p',{role:'status'},[401,403].includes(error.status)?'连接所有者后可读取会话信息。':'会话信息暂不可用；请重新核对。未发送或继续任何任务。'));
    }finally{
      if(isCurrent()){content.removeAttribute('aria-busy');refresh.disabled=false;if(focus)refresh.focus();}
    }
  }
  await load();
}

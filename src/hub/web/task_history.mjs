import {el,button,api} from './common.js';

const nonempty=value=>typeof value==='string'&&value.trim().length>0;
const bound=task=>task?.kind==='ordinary_project'&&nonempty(task.project)&&nonempty(task.thread_id);
const order=task=>Number.isSafeInteger(task.sequence)&&task.sequence>=0?task.sequence:Infinity;

export function relatedOrdinaryTasks(task,tasks){
  if(!bound(task)||!Array.isArray(tasks))return [];
  return tasks.filter(row=>bound(row)&&row.project===task.project&&row.thread_id===task.thread_id&&nonempty(row.id))
    .sort((a,b)=>order(a)-order(b)||a.id.localeCompare(b.id));
}

export function renderTaskHistory(task,{request=api,isCurrent=()=>true,linkFor}={}){
  const detail=el('details',{className:'panel stack'},el('summary',{},'本任务会话与历史'));
  if(!bound(task)){
    detail.append(el('p',{},'本任务尚无已核实的项目会话绑定；不会用其他 Hub 会话代替。'));
    return detail;
  }
  const binding=el('dl',{});
  for(const [label,value]of [['项目',task.project],['绑定会话',task.thread_id],['本次回合',task.turn_id||'尚未绑定']])
    binding.append(el('div',{},el('dt',{},label),el('dd',{},value)));
  const content=el('div',{className:'stack'},binding,
    el('p',{className:'caption'},'这里只核对 Hub 已登记、同一项目且绑定同一会话的任务结果；不会读取其他原生会话。'));
  const status=el('p',{role:'status','aria-live':'polite'}),list=el('div',{className:'stack'});
  const read=button('读取同会话任务结果',async()=>{
    if(read.disabled||!isCurrent())return;
    const restoreFocus=document.activeElement===read;
    read.disabled=true;status.textContent='正在读取同会话任务结果…';
    try{
      const data=await request('/api/tasks');
      if(!isCurrent()||!detail.isConnected)return;
      if(data?.tasks_available===false||!Array.isArray(data?.tasks))throw new Error('TASK_HISTORY_UNAVAILABLE');
      const rows=relatedOrdinaryTasks(task,data.tasks);
      const links=rows.map(row=>{
        const current=row.id===task.id&&row.result_version===task.result_version;
        return el('a',{href:linkFor(row),'aria-current':current?'page':null},
          (current?'本页任务结果：':row.id===task.id?'本任务当前记录（本页结果仍保留）：':'查看任务结果：')+row.id);
      });
      list.replaceChildren(...links);
      status.textContent=rows.length?'已核对 '+rows.length+' 条同会话任务记录。':'现有可读任务记录中没有匹配此项目与会话的结果；这不代表外部会话没有历史。';
    }catch{
      if(isCurrent()&&detail.isConnected)status.textContent='暂时无法读取会话历史；当前任务结果仍保留。请重新核对身份或连接后显式重读。';
    }finally{
      if(isCurrent()&&detail.isConnected){
        read.disabled=false;
        if(restoreFocus&&document.activeElement===document.body)read.focus();
      }
    }
  });
  content.append(read,status,list);detail.append(content);
  return detail;
}

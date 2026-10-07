import {el,button,api} from './common.js';

const names={cpu_usage:'CPU 使用率',cpu_cores:'逻辑核心',cpu_temperature:'CPU 温度',memory:'可用内存',swap:'交换空间',gpu_memory:'GPU 显存',gpu_usage:'GPU 使用率',gpu_temperature:'GPU 温度',gpu_encode:'GPU 编码占用',gpu_decode:'GPU 解码占用',disk_hub:'Hub 磁盘余量',disk_temp:'临时目录磁盘余量',io_hub:'Hub 所在设备 I/O',io_temp:'临时目录所在设备 I/O',hub_service:'Hub 服务',sample_duration:'一次采集耗时'};
const gib=n=>(n/1024**3).toLocaleString('zh-CN',{maximumFractionDigits:2})+' GiB';
export function metricText(key,metric){
  const v=metric?.value;
  if(v===null||v===undefined)return metric?.reason==='FIRST_SAMPLE'?'等待第二次采样':'暂不可用';
  if(key==='cpu_usage')return v+'%';
  if(key==='cpu_cores')return v+' 核';
  if(key==='cpu_temperature')return v.map(s=>`${s.name} ${s.label}：${s.value} °C`).join('\n');
  if(key.startsWith('gpu_'))return v.map(g=>`${g.name}：${g.value===null?'该指标不支持':key==='gpu_memory'?`${gib(g.value.used)} 已用 / ${gib(g.value.total)}`:g.value+(key==='gpu_temperature'?' °C':'%')}`).join('\n');
  if(key.startsWith('io_'))return `读取 ${(v.read/1024**2).toFixed(2)} MiB/s · 写入 ${(v.write/1024**2).toFixed(2)} MiB/s`;
  if(key==='sample_duration')return v+' ms';
  if(key==='memory'||key.startsWith('disk_'))return `${gib(v.available)} / ${gib(v.total)}`;
  if(key==='swap')return v.total===0?'未配置交换空间':`${gib(v.free)} 可用 / ${gib(v.total)}`;
  if(key==='hub_service')return `${({active:'运行中',inactive:'未运行',failed:'失败',activating:'启动中',deactivating:'停止中',reloading:'重载中'})[v.active]||'状态未知'} · ${v.sub} · PID ${v.pid}`;
  return '暂不可用';
}
export function metricState(metric,elapsed=0){
  if(!metric)return 'unavailable';
  if(metric.state!=='fresh')return metric.state;
  return !Number.isFinite(metric.age_seconds)||metric.age_seconds+elapsed>metric.max_age_seconds?'stale':'fresh';
}
export function renderHost(target,data){
  const metrics=data.metrics||{},grid=el('div',{className:'host-grid'}),labels=[];
  const started=performance.now();
  for(const [key,name] of Object.entries(names)){
    const m=metrics[key];
    const stamp=m?.observed_at?new Date(m.observed_at).toLocaleTimeString('zh-CN'):'尚未采样';
    const label=el('p',{});labels.push([m,label,stamp]);
    const detail=el('details',{},el('summary',{},'采样来源'),el('p',{},m?.source||'采集器尚未运行'),
      el('p',{},`单位：${m?.unit||'未知'} · 过期阈值：${m?.max_age_seconds??'未知'} 秒`),
      el('p',{},m?.reason||'正常采样'));
    if(m?.value?.mount)detail.append(el('p',{},`${m.value.mount.source} · ${m.value.mount.target} · ${m.value.mount.fstype}`));
    if(key.startsWith('gpu_')&&Array.isArray(m?.value))for(const g of m.value)detail.append(el('p',{},g.id));
    if(key==='cpu_temperature'&&Array.isArray(m?.value))for(const s of m.value)detail.append(el('p',{},s.path));
    grid.append(el('article',{className:'panel stack host-metric'},el('h2',{},name),
      el('p',{className:'host-value'},metricText(key,m)),label,detail));
  }
  const notice=el('p',{className:'warning',role:'status'});
  const refresh=button('读取最新采样',async()=>{
    refresh.disabled=true;
    try{
      const updated=await api('/api/host');
      if(section.isConnected){renderHost(target,updated);target.querySelector('button')?.focus();}
    }catch{if(section.isConnected)notice.textContent='后端暂时无法连接；保留最后观测，请重连后重新读取。';}
    finally{refresh.disabled=false;if(section.isConnected)refresh.focus();}
  });
  const section=el('section',{className:'stack'},el('h1',{},'Linux 主机'),
    el('p',{className:'muted'},'主机观测独立于项目进度。服务运行状态不能证明任务成功。'),
    el('div',{className:'row'},refresh,el('p',{className:'caption'},`后台采样间隔：${data.interval_seconds??'未知'} 秒`)),notice,
    grid,el('p',{className:'caption'},'磁盘 I/O 是路径所在设备的观测；Hub 与临时目录可能共用同一设备。编码/解码占用不证明业务编解码成功。'),
    el('p',{className:'caption'},`采集耗时的初始目标：${data.initial_sample_target_ms??'未知'} ms。并发容量：未知 · 尚无已测任务峰值画像。`));
  target.replaceChildren(section);
  // Age the displayed snapshot while it remains open. This makes collector loss
  // visible without confusing the browser's lifetime with the collector's.
  function age(){
    if(!section.isConnected)return;
    for(const [m,label,stamp] of labels){
      const state=metricState(m,(performance.now()-started)/1000);
      label.className=state==='fresh'?'caption':'warning';
      label.textContent=`${({fresh:'最新采样',stale:'已过期 · 保留最后观测',unavailable:'不可用'})[state]||'状态未知'} · ${stamp}`;
    }
    setTimeout(age,1000);
  }
  age();
}

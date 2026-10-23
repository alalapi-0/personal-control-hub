import {el} from './common.js';
import {businessPlan} from './business_tasks.mjs';
import {projectSummary,currentSourceAvailable} from './connection_view.mjs';

const DAY=86400000,MIN=Date.parse('0001-01-01T00:00:00Z')/DAY,MAX=Date.parse('9999-12-31T00:00:00Z')/DAY;
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const text=v=>typeof v==='string'&&v.length>0;
const date=v=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&v.slice(0,4)!=='0000'&&Number.isFinite(Date.parse(v+'T00:00:00Z'))&&new Date(Date.parse(v+'T00:00:00Z')).toISOString().slice(0,10)===v;
const dayText=d=>new Date(d*DAY).toISOString().slice(0,10);
const selector=v=>v&&typeof v==='object'&&!Array.isArray(v)&&((Object.keys(v).length===1&&Array.isArray(v.path)&&v.path.length>0&&v.path.length<=20&&v.path.every(x=>text(x)||(Number.isSafeInteger(x)&&x>=0)))||(Object.keys(v).length===2&&text(v.heading)&&((text(v.label))||(text(v.table_label))||(Number.isSafeInteger(v.ordered_item)&&v.ordered_item>0))));
function point(p){
  if(!p||!['date','timestamp'].includes(p.kind)||(p.timezone!==null&&!text(p.timezone)))return null;
  if(p.timezone!==null){try{new Intl.DateTimeFormat('en',{timeZone:p.timezone});}catch{return null;}}
  if(p.kind==='date')return date(p.value)?{...p,day:Date.parse(p.value+'T00:00:00Z')/DAY,axis:p.value,basis:'calendar',instant:null}:null;
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(p.value)||!date(p.value.slice(0,10)))return null;
  const instant=Date.parse(p.value);if(!Number.isFinite(instant))return null;
  const day=Math.floor(instant/DAY);if(day<MIN||day>MAX)return null;
  return {...p,day,axis:dayText(day),basis:'UTC',instant};
}
const comparable=(a,b)=>!!a&&!!b&&(a.kind===b.kind)&&(a.kind==='timestamp'||a.timezone===b.timezone)&&(a.kind==='timestamp'?a.instant<=b.instant:a.day<=b.day);
function lane(kind,label,start,end,milestones=[]){
  const a=point(start),b=point(end),ms=Array.isArray(milestones)?milestones.map(m=>({id:m.id,label:m.label,at:point(m.at)})):[];
  return {kind,label,start:a,end:b,milestones:ms,continuous:comparable(a,b),points:[a,b,...ms.map(m=>m.at)].filter(Boolean),unscheduled:!a||!b||ms.some(m=>!m.at)};
}
const statuses={not_started:'未开始',active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',cancelled:'已取消',unknown:'未知'};
const reasons={TIMELINE_NOT_DECLARED:'来源尚未声明实际日期',SOURCE_STALE:'来源过期，实际日期未知',SOURCE_UNAVAILABLE:'最新来源读取不可用，实际日期未知',READ_DISABLED:'当前读取未获允许，实际日期不可查看'};
const reasonText=v=>reasons[v]??v;
function actual(project){
  const dto=project?.business?.timeline,record=project?.operational?.latest_attempt;
  if(project?.freshness?.local_presence==='removed_local'||project?.freshness?.state==='removed_local'||project?.declared?.connection_read_allowed!==true)return {value:null,reason:'来源不可读取或已移除'};
  if(record?.success!==true||record.schema_version!=='2.0'||record.project_id!==project.project_id||!text(record.observed_at))return {value:null,reason:'最新来源读取未成功'};
  if(project?.freshness?.state!=='fresh'||project?.freshness?.authority_drift===true)return {value:null,reason:'来源过期或当前读取规则已变化'};
  if(dto?.available!==true||dto.role!=='current'||!dto.value)return {value:null,reason:reasonText(dto?.reason)||'来源未提供当前实际日期'};
  const proof=dto.proof,field=record.extensions?.fact?.task_timeline;
  if(!['1.2','1.3','1.4'].includes(record.extensions?.schema_version)||!hash(proof?.sha256)||!text(proof.source_ref)||!selector(proof.selector)||proof.observed_at!==record.observed_at||!Array.isArray(record.sources)||record.sources.filter(s=>s.id===proof.source_ref&&s.sha256===proof.sha256&&(s.observed_at===undefined||s.observed_at===record.observed_at)).length!==1||field?.unknown_reason!==null||JSON.stringify(field?.proof)!==JSON.stringify(proof)||JSON.stringify(field?.value)!==JSON.stringify(dto.value))return {value:null,reason:'当前实际日期的来源证据不可核对'};
  const v=dto.value;if(v.schema_version!=='1.0'||v.project_id!==project.project_id||!Array.isArray(v.task_dates)||!Array.isArray(v.milestones))return {value:null,reason:'实际日期格式不可核对'};
  return {value:v,reason:null,proof:structuredClone(proof)};
}

function forecastLanes(plan,tz){
  if(!plan||plan.schema_version!=='1.0'||plan.kind!=='rolling_estimate')return [];
  const windowLane=(id,label,window,subkind,extra={})=>{
    const valid=window&&date(window.earliest)&&date(window.latest)&&window.earliest<=window.latest;
    const l=lane('forecast',label,valid?{kind:'date',value:window.earliest,timezone:tz}:null,valid?{kind:'date',value:window.latest,timezone:tz}:null);
    return {...l,id,subkind,forecast:true,...extra,reason:valid?null:'尚无法估计日期窗口；保留条件，不生成完成日期'};
  };
  const dependencyLabels=new Map((plan.acceptance_windows??[]).map(a=>[a.id,`${a.kind==='human'?'历史审核窗口':'自动核验建议窗口'}：${a.label}`]));if(plan.next_milestone)dependencyLabels.set(plan.next_milestone.id,`下一里程碑：${plan.next_milestone.label}`);
  const lanes=[windowLane('completion','预计完成窗口（非截止日期）',plan.completion_window,'completion')];
  if(plan.next_milestone)lanes.push(windowLane(plan.next_milestone.id,`预计下一里程碑：${plan.next_milestone.label}`,plan.next_milestone.window,'milestone',{condition:plan.next_milestone.condition}));
  for(const acceptance of plan.acceptance_windows??[])lanes.push(windowLane(acceptance.id,`${acceptance.kind==='human'?'历史审核窗口':'自动核验建议窗口'}：${acceptance.label}`,acceptance.window,acceptance.kind,{historical:acceptance.kind==='human',scope:acceptance.scope,dependsOn:acceptance.depends_on??[],dependencyRole:'historical_forecast_association',dependsOnLabels:(acceptance.depends_on??[]).map(id=>dependencyLabels.get(id)??'原估计中的关联项')}));
  return lanes;
}
function engineeringVerification(project){
  const r=project?.operational?.latest_attempt,v=project?.business?.fields?.verification?.status,proof=r?.field_provenance?.['verification.status'];
  const current=project?.declared?.connection_read_allowed===true&&project?.freshness?.state==='fresh'&&project?.freshness?.authority_drift!==true&&r?.success===true&&r.project_id===project.project_id;
  if(!current||!text(v)||r?.business?.verification?.status!==v||!hash(proof?.sha256)||!text(proof.source_ref)||!selector(proof.selector)||!r.sources?.some(s=>s.id===proof.source_ref&&s.sha256===proof.sha256))return null;
  return v;
}

// Each input is one complete directory row. Unknown and removed identities stay
// present; only explicitly declared, current source dates enter the actual lane.
export function timelineRows(projects){
  return projects.map(project=>{
    const source=actual(project),management=project.management,overlay=management?.available===true?management.overlay:null;
    const tz=overlay?.timezone??null;
    const planned=lane('planned','Hub 管理建议日期',overlay?.start_date?{kind:'date',value:overlay.start_date,timezone:tz}:null,overlay?.end_date?{kind:'date',value:overlay.end_date,timezone:tz}:null,(overlay?.milestones||[]).map(m=>({...m,at:m.date?{kind:'date',value:m.date,timezone:tz}:null})));
    planned.reason=management?.available!==true?'管理配置不可读取':overlay===null||overlay===undefined?'管理计划未配置':planned.unscheduled?'管理计划日期未全部声明':null;
    const v=source.value,observed=lane('actual','来源实际',v?.actual_start,v?.actual_end,v?.milestones);observed.reason=source.reason;observed.proof=source.proof??null;
    const planView=businessPlan(project),plan=planView.plan;
    const dates=v&&plan&&v.plan_version===plan.plan_version?new Map(v.task_dates.map(d=>[d.task_id,d])):new Map();
    const stages=(plan?.nodes||[]).filter(n=>n.kind==='stage').map(n=>{const d=dates.get(n.id),entry=lane('stage',plan.labels[n.id],d?.actual_start,d?.actual_end,d?.milestones);return {...entry,id:n.id,status:n.status,reason:!d?'来源未声明该阶段的实际日期':entry.unscheduled?'阶段日期未全部声明':null};});
    const rollingPlan=overlay?.rolling_plan??null,allForecasts=forecastLanes(rollingPlan,tz),forecasts=allForecasts.filter(l=>!l.historical),historicalWindows=allForecasts.filter(l=>l.historical),lanes=[planned,observed,...forecasts,...stages],points=lanes.flatMap(l=>l.points);
    // Historical human windows remain intact, but never enter current date,
    // unscheduled/work counts or any execution/phase-exit dependency projection.
    return {id:project.project_id,name:project.name??project.project_id,project,planned,actual:observed,forecasts,historicalWindows,rollingPlan:allForecasts.length?rollingPlan:null,verification:engineeringVerification(project),stages,points,dated:points.length>0,unscheduled:lanes.some(l=>l.unscheduled),phase:project.business?.fields?.current_work?.phase??null,phaseReason:plan?null:planView.reason};
  });
}
function svg(tag,attrs={},...children){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));for(const c of children)n.appendChild(typeof c==='string'?document.createTextNode(c):c);return n;}
const literal=p=>p?`${p.value} · ${p.kind==='timestamp'?'时间戳，坐标 UTC 日':'日历日期'} · 原始日历时区 ${p.timezone??(p.kind==='date'?'未声明':'原始偏移')}（坐标 ${p.axis}）`:'未知 · 未排期';
const confidenceLabels={unknown:'未知',low:'低',medium:'中',high:'高'};
const laneClasses=l=>`timeline-${l.kind}${l.forecast?` timeline-forecast-${l.subkind}`:''}`;
const detail=id=>`#projects/${encodeURIComponent(id)}`;

export function renderTimeline(projects,{total=projects.length,onEdit,context='directory'}={}){
  const projectContext=context==='project';
  const rows=timelineRows(projects),allPoints=rows.flatMap(r=>r.points),earliest=allPoints.length?allPoints.reduce((a,p)=>Math.min(a,p.day),MAX):null;
  let mode='all',zoom='week',anchor=earliest;const focusTargets=new Map();
  const section=el('section',{className:'panel stack timeline','aria-label':projectContext?'当前项目时间线':'完整目录时间线'},el('h2',{},projectContext?'当前项目日期与未排期阶段':'目录时间线与未排期阶段'),el('p',{className:'caption'},projectContext?`当前项目 ${rows.length} 项 · 管理建议日期与来源实际分别展示；历史审核不计当前待办`:`目录筛选可见 ${rows.length} / 完整目录 ${total} 个项目 · 管理建议日期与来源实际分别展示；历史审核不计当前待办`));
  const filter=el('select',{'aria-label':'时间线日期筛选'},...Object.entries({all:'全部项目',dated:'有当前已知或建议日期',unscheduled:'含当前未排期项目或阶段'}).map(([value,label])=>el('option',{value},label)));
  const scale=el('select',{'aria-label':'时间线缩放'},...Object.entries({day:'日视图',week:'周视图',month:'月视图'}).map(([value,label])=>el('option',{value},label)));scale.value=zoom;
  const prev=el('button',{type:'button',onClick:()=>move(-1)},'上一个日期窗口'),next=el('button',{type:'button',onClick:()=>move(1)},'下一个日期窗口');
  const jump=el('input',{type:'date','aria-label':'跳转到日历日期',min:'0001-01-01',max:'9999-12-31'});
  const jumpButton=el('button',{type:'button',onClick:()=>{if(date(jump.value)){anchor=Date.parse(jump.value+'T00:00:00Z')/DAY;draw();}}},'跳转日期');
  const status=el('p',{role:'status',className:'caption'}),axis=el('div',{className:'timeline-axis'}),content=el('div',{className:'stack timeline-rows'});
  section.append(el('div',{className:'timeline-controls'},el('label',{},'日期筛选',filter),el('label',{},'缩放',scale),prev,next,el('label',{},'窗口日期',jump),jumpButton),status,axis,content);
  filter.addEventListener('change',()=>{mode=filter.value;draw();});scale.addEventListener('change',()=>{zoom=scale.value;draw();});
  const span=()=>({day:14,week:84,month:365}[zoom]??84);
  function bounds(){const s=span(),a=anchor===null?null:Math.max(MIN,Math.min(MAX-s+1,anchor));return [a,a===null?null:a+s-1];}
  function move(direction,focusKey=null){if(anchor!==null){const[a]=bounds();anchor=Math.max(MIN,Math.min(MAX-span()+1,a+direction*span()));draw(focusKey);}}
  function datesText(l,id){
    const dates=el('div',{className:'timeline-date-text'},el('p',{},`${l.label} · ${l.historical?'历史预测／审核记录；不参与当前状态、依赖或阶段退出':l.forecast?'滚动估计；窗口不代表实际工期或硬性截止日期':l.continuous?'可比较的开始与结束':'独立日期标记，不计算连续时长'}`));
    for(const [label,p]of [[l.forecast?'窗口最早':l.kind==='planned'?'建议开始':'实际开始',l.start],[l.forecast?'窗口最晚':l.kind==='planned'?'建议结束':'实际结束',l.end],...l.milestones.map(m=>[`里程碑 ${m.label}（${m.id}）`,m.at])]){
      const entry=el('p',{className:'timeline-point'},`${label}：`,p?el('a',{href:detail(id)},literal(p)):l.historical?'历史未记录日期（不计当前未排期）':literal(null));
      if(p){const key=JSON.stringify(['point',id,l.kind,l.id??null,label,p.value]),button=el('button',{type:'button','aria-label':`定位 ${id} ${l.label} ${label} ${p.value}`,onClick:()=>{anchor=p.day;draw(key);}},'定位日期');focusTargets.set(key,button);entry.append(button);}
      dates.append(entry);
    }
    if(l.condition)dates.append(el('p',{},`前置条件：${l.condition}`));
    if(l.scope)dates.append(el('p',{},`原估计适用范围：${l.scope}`));
    if(l.dependsOn?.length)dates.append(el('p',{},`历史预测关联（非执行依赖）：${(l.dependsOnLabels??l.dependsOn).join('、')}`));
    if(l.historical)dates.append(el('p',{},'这是旧 human 预测／审核窗口，仅保留历史身份；流程外本人回看不参与状态、依赖或阶段退出。'));
    else if(l.forecast&&l.subkind==='automatic')dates.append(el('p',{},'这是自动核验建议窗口，尚非工程结果或代理决定记录。'));
    if(l.reason)dates.append(el('p',{className:'caption'},l.historical?'历史窗口未记录日期；不是当前待办。':`未知原因：${l.reason}`));
    if(l.proof)dates.append(el('details',{},el('summary',{},'查看日期来源'),el('p',{className:'caption'},`来源 ${l.proof.source_ref} · 观察 ${l.proof.observed_at} · SHA256 ${l.proof.sha256} · 选择器 ${JSON.stringify(l.proof.selector)}`)));
    return dates;
  }
  function chart(lanes,a,b,id){
    const drawing=svg('svg',{viewBox:`0 0 640 ${lanes.length*32+16}`,width:'640',class:'timeline-chart',role:'img','aria-label':`${id} 日期标记，日历日期保留自身日历基准；时间戳使用 UTC 日坐标`});
    const x=d=>12+(d-a)/Math.max(1,b-a)*616;
    lanes.forEach((l,index)=>{const y=16+index*32;drawing.appendChild(svg('line',{x1:12,x2:628,y1:y,y2:y,class:'timeline-lane'}));
      if(l.continuous&&l.end.day>=a&&l.start.day<=b)drawing.appendChild(svg('rect',{x:x(Math.max(a,l.start.day)),y:y-4,width:Math.max(1,x(Math.min(b,l.end.day))-x(Math.max(a,l.start.day))),height:8,class:`timeline-bar ${laneClasses(l)}`},svg('title',{},`${l.label}：${literal(l.start)} → ${literal(l.end)}${l.start.day<a||l.end.day>b?'（超出窗口的部分未显示）':''}`)));
      for(const [label,p]of [[l.forecast?'窗口最早':l.kind==='planned'?'建议开始':'实际开始',l.start],[l.forecast?'窗口最晚':l.kind==='planned'?'建议结束':'实际结束',l.end],...l.milestones.map(m=>[m.label,m.at])])if(p&&p.day>=a&&p.day<=b)drawing.appendChild(svg('circle',{cx:x(p.day),cy:y,r:5,class:`timeline-marker ${laneClasses(l)}`},svg('title',{},`${l.label} ${label}：${literal(p)}`)));
    });return drawing;
  }
  function draw(focusKey=null){
    focusTargets.clear();
    const visible=rows.filter(r=>mode==='all'||(mode==='dated'?r.dated:r.unscheduled)),[a,b]=bounds();
    jump.value=a===null?'':dayText(a);
    prev.disabled=a===null||a===MIN;next.disabled=a===null||b===MAX;
    status.textContent=`${projectContext?`当前项目日期筛选显示 ${visible.length} / 当前项目 ${rows.length}`:`时间线筛选显示 ${visible.length} / 目录筛选 ${rows.length} / 完整目录 ${total}`} · ${a===null?'没有当前已知日期；未推断当前日期':`窗口 ${dayText(a)} → ${dayText(b)} · 日历日期按原日历，时间戳按 UTC 日`}`;
    axis.replaceChildren();if(a!==null){const ticks=svg('svg',{viewBox:'0 0 640 44',width:'640',class:'timeline-chart',role:'img','aria-label':'当前日期窗口'});for(let i=0;i<=4;i++)ticks.appendChild(svg('text',{x:12+i*154,y:26,'text-anchor':i===0?'start':i===4?'end':'middle'},dayText(a+Math.floor((b-a)*i/4))));axis.append(ticks);}
    content.replaceChildren();
    for(const r of visible){
      const lanes=[r.planned,r.actual,...r.forecasts,...r.stages],article=el('article',{className:'stack timeline-project','data-project-id':r.id},el('h3',{},el('a',{href:detail(r.id)},`${r.name}（${r.id}）`)));
      const sourceSummary=projectSummary(r.project),facts=Object.fromEntries(sourceSummary.fields),current=currentSourceAvailable(r.project);
      article.append(el('p',{className:'caption'},`${current?'当前来源':sourceSummary.historical?'历史快照':'当前来源不可用'}：${facts['工作状态']} · 业务进度 ${facts['业务进度 V/N']}`));
      if(!current)article.append(el('p',{className:'caption'},sourceSummary.quality));
      if(facts['计数依据']!=='未知')article.append(el('details',{},el('summary',{},'查看进度计数依据'),el('p',{className:'caption'},facts['计数依据'])));
      if(r.rollingPlan){
        const rp=r.rollingPlan,summary=el('section',{className:'stack timeline-forecast-summary','aria-label':`${r.id} 滚动估计说明`},el('h4',{},'滚动估计（非截止日期）'),el('p',{},`估计基准 ${rp.as_of} · 下次复核 ${rp.next_review} · 信心 ${confidenceLabels[rp.confidence]??'未知'} · 时区 ${r.project.management.overlay.timezone??'未声明'}`));
        summary.append(el('p',{},'建议窗口保留原估计基准；来源实际日期、工程结果、代理决定和流程外本人回看分别说明。'));
        summary.append(el('p',{},'来源状态与计数以上方摘要为准；以下依据和适用范围保留估计基准时的记录，不随来源刷新自动改写；过期预测也不因此成为今日新估计。'));
        for(const condition of rp.conditions??[])summary.append(el('p',{},`估计条件：${condition}`));
        for(const basis of rp.basis??[])summary.append(el('details',{},el('summary',{},`估计基准 ${rp.as_of} 的${basis.kind==='source'?'来源依据':'估计假设'}：${basis.summary}`),el('p',{className:'caption'},`引用 ${basis.reference}${basis.sha256?` · SHA256 ${basis.sha256}`:''}`)));
        article.append(summary);
      }
      article.append(el('p',{className:'caption'},`工程结果：${r.verification?'已有来源记录，可展开查看':'未知'}；工程核验日期：未知／未提供独立记录。`),el('p',{className:'caption'},'代理决定：未知／未提供独立记录。'),el('p',{className:'caption'},'流程外本人回看，不参与状态、依赖或阶段退出。'));
      if(r.verification)article.append(el('details',{},el('summary',{},'查看来源工程核验状态'),el('p',{className:'caption'},`来源工程核验状态：${r.verification}`)));
      if(onEdit)article.append(el('button',{type:'button',onClick:()=>onEdit(r.project),'aria-label':`编辑 ${r.id} 管理计划`},'编辑管理计划'));
      if(a!==null&&r.dated){const scroller=el('div',{className:'timeline-scroll',tabIndex:0,role:'region','aria-label':`${r.id} 日期图；左右方向键切换日期窗口`,onKeydown:e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();move(e.key==='ArrowLeft'?-1:1,'region:'+r.id);}}},...lanes.filter(l=>l.points.length).map(l=>el('div',{className:'stack'},el('p',{className:'caption'},l.label),chart([l],a,b,r.id))));focusTargets.set('region:'+r.id,scroller);article.append(scroller);}
      for(const l of lanes){const box=el('section',{className:`timeline-lane-text ${laneClasses(l)}`},datesText(l,r.id));if(l.kind==='stage')box.prepend?box.prepend(el('h4',{},`来源阶段 ${l.label} · ${statuses[l.status]??'未知'}`)):box.append(el('h4',{},`来源阶段 ${l.label} · ${statuses[l.status]??'未知'}`));article.append(box);}
      if(r.historicalWindows.length){
        const history=el('section',{className:'stack','aria-label':`${r.id} 历史审核窗口（非当前待办）`},el('h4',{},'历史审核窗口 · 仅保留原预测与审核身份'),el('p',{className:'caption'},`原估计基准 ${r.rollingPlan.as_of}；原始窗口、范围、依据与预测关联保持，不进入当前未排期、执行依赖或阶段退出。`));
        for(const historical of r.historicalWindows)history.append(datesText(historical,r.id));
        article.append(history);
      }
      if(!r.stages.length)article.append(el('p',{className:'caption'},`来源阶段：${r.phase??'未知'} · ${r.phaseReason??'当前来源计划未列出阶段'}；未推断为零完成。`));
      const outside=r.points.filter(p=>a!==null&&(p.day<a||p.day>b)).length;if(outside)article.append(el('p',{className:'caption'},`${outside} 个已知日期在窗口外，原始日期与定位按钮完整保留。`));
      content.append(article);
    }
    if(!visible.length)content.append(el('p',{},projectContext?'当前日期筛选没有匹配的项目日期。':'当前日期筛选没有项目。完整目录计数仍保留。'));
    if(focusKey)focusTargets.get(focusKey)?.focus?.();
  }
  draw();return section;
}

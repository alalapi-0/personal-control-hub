import {el} from './dom.mjs';

// A local view of one validated reader snapshot. No fetching or source writes.
export const objectKey=value=>JSON.stringify([value.id??value.object_id,value.revision]);
const labels={Project:'项目',Goal:'目标',Phase:'阶段',Round:'轮次',Task:'任务',Artifact:'产物',FileRef:'文件引用',Workflow:'工作流',Run:'运行',Service:'服务',Tool:'工具',Decision:'决定',Review:'评审'};
const relations={managed_by:'管理归属',part_of:'属于',depends_on:'依赖',blocks:'阻塞',consumes:'使用',produces:'产出',derived_from:'派生自',references:'引用',related_to:'相关'};
const finite=(v,fallback,min,max)=>typeof v==='number'&&Number.isFinite(v)?Math.max(min,Math.min(max,v)):fallback;
const text=v=>typeof v==='string'?v.slice(0,4096):'未知';
export function relationText(edge){return `${relations[edge.type]||'未知关系'} · ${edge.identity==='candidate'?(edge.management?.state==='accepted'?'已确认管理覆盖，仍非来源事实':'候选关系'):'来源事实'}`;}
export function graphViewState(snapshot,input){
  const keys=new Set(snapshot.entries.map(e=>e.key));
  const valid=input&&input.projectId===snapshot.projectId&&input.projectionVersion===snapshot.projectionVersion?input:{};
  const grouping=['type','parent','none'].includes(valid.grouping)?valid.grouping:'type';
  const expanded=Array.isArray(valid.collapsed)?[...new Set(valid.collapsed.slice(0,256).filter(x=>typeof x==='string'&&x.length<=1024))].slice(0,256):[];
  const pins=[];
  if(Array.isArray(valid.pins))for(const p of valid.pins.slice(0,1000))if(p&&keys.has(p.key)&&!pins.some(v=>v.key===p.key)&&Number.isFinite(p.x)&&Number.isFinite(p.y))pins.push({key:p.key,x:finite(p.x,0,0,100000),y:finite(p.y,0,0,100000)});
  return {projectId:snapshot.projectId,projectionVersion:snapshot.projectionVersion,selected:keys.has(valid.selected)?valid.selected:null,
    viewport:{x:finite(valid.viewport?.x,0,-100000,100000),y:finite(valid.viewport?.y,0,-100000,100000),zoom:finite(valid.viewport?.zoom,1,.2,3)},
    grouping,collapsed:expanded,pins,relatedOnly:valid.relatedOnly===true};
}
// Pending history is bounded view data, never renderable until a fresh snapshot validates it.
export function pendingGraphView(input,projectId){
  if(!input||input.projectId!==projectId||typeof input.projectionVersion!=='string'||!/^([a-f0-9]{64})$/.test(input.projectionVersion))return null;
  const keyOK=k=>{if(typeof k!=='string'||k.length>8192)return false;try{const v=JSON.parse(k);return Array.isArray(v)&&v.length===2&&typeof v[0]==='string'&&v[0].length>0&&v[0].length<=4096&&(v[1]===null||typeof v[1]==='string'&&v[1].length<=4096);}catch{return false;}};
  const keys=new Set();if(keyOK(input.selected))keys.add(input.selected);
  if(Array.isArray(input.pins))for(const p of input.pins.slice(0,1000))if(p&&keyOK(p.key))keys.add(p.key);
  return graphViewState({projectId,projectionVersion:input.projectionVersion,entries:[...keys].map(key=>({key}))},input);
}
export function focusModel(snapshot,view){
  const entries=snapshot.entries,byKey=new Map(entries.map(e=>[e.key,e])),edges=snapshot.relations||[];
  const parents=new Map();for(const edge of edges)if(edge.type==='part_of'&&!parents.has(objectKey(edge.from)))parents.set(objectKey(edge.from),objectKey(edge.to));
  const groups=new Map(),groupFor=new Map();
  for(const entry of entries){
    const parent=parents.get(entry.key),id=view.grouping==='none'?'all':view.grouping==='parent'?(parent||'root'):entry.object.type;
    if(!groups.has(id))groups.set(id,{id,label:view.grouping==='none'?'全部对象':view.grouping==='parent'?(byKey.get(id)?.row.label||'无归属对象'):(labels[id]||id),entries:[]});
    groups.get(id).entries.push(entry);groupFor.set(entry.key,id);
  }
  const allowed=new Set(entries.map(e=>e.key));
  if(view.relatedOnly&&view.selected){allowed.clear();allowed.add(view.selected);for(const edge of edges){const a=objectKey(edge.from),b=objectKey(edge.to);if(a===view.selected)allowed.add(b);if(b===view.selected)allowed.add(a);}}
  const pins=new Map(view.pins.map(p=>[p.key,p])),published=new Map((snapshot.layouts||[]).filter(l=>l.view_id==='focus').map(l=>[JSON.stringify([l.object_id,l.object_revision]),l]));
  const nodes=[],sections=[];let y=36;
  for(const group of groups.values()){
    const members=group.entries.filter(e=>allowed.has(e.key)),collapsed=view.collapsed.includes(group.id);
    if(!members.length)continue;
    const section={...group,y,count:members.length,collapsed};sections.push(section);y+=42;
    if(!collapsed){let rowY=y,rowHeight=0;members.forEach((entry,i)=>{
      if(i&&i%3===0){rowY+=rowHeight+24;rowHeight=0;}
      const lines=wrapLabel(entry.row.label),width=240,height=70+lines.length*20,pin=pins.get(entry.key),layout=published.get(entry.key);
      const x=pin?.x??layout?.x??24+(i%3)*280,ny=pin?.y??layout?.y??rowY;
      nodes.push({...entry,x,y:ny,width,height,lines,pinned:!!pin,group:group.id});rowHeight=Math.max(rowHeight,height);
    });y=rowY+rowHeight+36;}
  }
  const visible=new Set(nodes.map(n=>n.key));
  return {nodes,groups:sections,edges:edges.filter(e=>visible.has(objectKey(e.from))&&visible.has(objectKey(e.to))),
    hiddenEdges:edges.filter(e=>!visible.has(objectKey(e.from))||!visible.has(objectKey(e.to))).length,
    crossGroup:edges.filter(e=>groupFor.get(objectKey(e.from))!==groupFor.get(objectKey(e.to))).length,
    width:Math.max(860,...nodes.map(n=>n.x+n.width+24)),height:Math.max(280,y,...nodes.map(n=>n.y+n.height+24))};
}
function wrapLabel(value){
  const chars=Array.from(text(value)),lines=[];let line='',units=0,truncated=false;
  for(const char of chars){const weight=char.codePointAt(0)>255?2:1;
    if(units+weight>26){lines.push(line);line='';units=0;if(lines.length===4){truncated=true;break;}}
    line+=char;units+=weight;
  }
  if(line&&lines.length<4)lines.push(line);if(truncated)lines[3]+='…';return lines.length?lines:['未命名'];
}
const svgNode=(tag,attrs={},content=null)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(content!==null)n.textContent=content;return n;};
export function createFocusGraph(snapshot,{saved,onChange=()=>{},onProject=()=>{}}={}){
  let view=graphViewState(snapshot,saved),model=null,disposed=false,drag=null;
  const lifetime=new AbortController();let drawing=new AbortController(),observer=null;
  let size={width:900,height:460};
  const listen=(node,event,fn,signal=lifetime.signal)=>node.addEventListener(event,fn,{signal});
  const root=el('section',{className:'focus-graph stack','aria-label':'项目聚焦图与同源列表'}),status=el('p',{className:'caption',role:'status'});
  const toolbar=el('div',{className:'focus-graph-controls','aria-label':'图视图控制'}),canvas=svgNode('svg',{class:'focus-graph-canvas',viewBox:'0 0 900 460',role:'group','aria-label':'项目对象关系图，方向键移动选择，Shift方向键移动并固定节点，空白处拖动平移',tabindex:'0'});
  const scene=svgNode('g'),list=el('ul',{className:'focus-graph-list'}),detail=el('div',{className:'focus-graph-detail','aria-live':'polite'});
  const groupSelect=el('select',{'aria-label':'图对象分组'},...['type','parent','none'].map((v,i)=>el('option',{value:v},['按对象类型','按归属关系','不分组'][i])));
  groupSelect.value=view.grouping;
  const related=el('input',{type:'checkbox','aria-label':'只看选中对象及直接关系'});related.checked=view.relatedOnly;
  const control=(name,fn)=>{const b=el('button',{type:'button'},name);listen(b,'click',fn,drawingButtons?drawing.signal:lifetime.signal);return b;};
  let drawingButtons=false;
  const emit=()=>{if(!disposed)onChange(graphViewState(snapshot,view));};
  const transform=()=>scene.setAttribute('transform',`translate(${view.viewport.x} ${view.viewport.y}) scale(${view.viewport.zoom})`);
  function move(dx,dy){view.viewport.x=finite(view.viewport.x+dx,0,-100000,100000);view.viewport.y=finite(view.viewport.y+dy,0,-100000,100000);transform();emit();}
  function zoom(mult){view.viewport.zoom=finite(view.viewport.zoom*mult,1,.2,3);transform();emit();}
  function select(key,focus=false){if(disposed)return;view.selected=snapshot.entries.some(e=>e.key===key)?key:null;draw(focus,true);emit();}
  function togglePin(){if(!view.selected)return;const node=model.nodes.find(n=>n.key===view.selected);if(!node)return;view.pins=view.pins.filter(p=>p.key!==node.key);if(!node.pinned&&view.pins.length<1000)view.pins.push({key:node.key,x:node.x,y:node.y});draw();emit();}
  function recenter(){const node=model?.nodes.find(n=>n.key===view.selected);if(!node)return;const v=view.viewport,z=v.zoom;
    if(node.x*z+v.x<0||((node.x+node.width)*z+v.x)>size.width)v.x=size.width/2-(node.x+node.width/2)*z;
    if(node.y*z+v.y<0||((node.y+node.height)*z+v.y)>size.height)v.y=size.height/2-(node.y+node.height/2)*z;
  }
  function measure(){if(disposed)return;const r=canvas.getBoundingClientRect();if(r.width<=0||r.height<=0)return;
    const previous={...view.viewport};
    size={width:finite(canvas.clientWidth||r.width,900,160,4096),height:finite(canvas.clientHeight||r.height,460,160,2048)};
    canvas.setAttribute('viewBox',`0 0 ${size.width} ${size.height}`);recenter();transform();
    if(previous.x!==view.viewport.x||previous.y!==view.viewport.y)emit();
  }
  function draw(focus=false,bringIntoView=focus){
    if(disposed)return;model=focusModel(snapshot,view);drawing.abort();drawing=new AbortController();scene.replaceChildren();list.replaceChildren();detail.replaceChildren();
    const positions=new Map(model.nodes.map(n=>[n.key,n]));
    for(const edge of model.edges){const a=positions.get(objectKey(edge.from)),b=positions.get(objectKey(edge.to));
      const line=svgNode('path',{d:`M ${a.x+a.width/2} ${a.y+a.height} L ${b.x+b.width/2} ${b.y}`,
        class:'focus-edge'+(edge.identity==='candidate'?' candidate':''),'marker-end':'url(#focus-arrow)'});
      line.append(svgNode('title',{},`${a.row.label} → ${b.row.label}：${relationText(edge)}`));scene.append(line);
    }
    for(const group of model.groups){
      const g=svgNode('g',{class:'focus-group',role:'button',tabindex:'0','aria-expanded':!group.collapsed,'data-group-key':group.id,'aria-label':`${group.label}，${group.count}项，${group.collapsed?'展开':'折叠'}`});
      g.append(svgNode('rect',{x:16,y:group.y-26,width:820,height:40,rx:6}),svgNode('text',{x:28,y:group.y},`${group.collapsed?'＋':'－'} ${text(group.label)} · ${group.count}`));
      const toggle=()=>{view.collapsed=view.collapsed.filter(x=>x!==group.id);if(!group.collapsed)view.collapsed.push(group.id);draw();const header=[...scene.querySelectorAll('[data-group-key]')].find(n=>n.getAttribute('data-group-key')===group.id);header?.focus({preventScroll:true});emit();};
      listen(g,'click',toggle,drawing.signal);listen(g,'keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();toggle();}},drawing.signal);scene.append(g);
    }
    drawingButtons=true;
    const selectedVisible=model.nodes.some(n=>n.key===view.selected),roving=selectedVisible?view.selected:model.nodes[0]?.key;
    for(const node of model.nodes){
      const g=svgNode('g',{class:`focus-node ${node.object.identity}${node.key===view.selected?' selected':''}`,transform:`translate(${node.x} ${node.y})`,role:'button',tabindex:node.key===roving?'0':'-1','aria-pressed':node.key===view.selected,'data-object-key':node.key,'aria-label':`${node.row.label}，${labels[node.object.type]}，${node.object.identity==='candidate'?'候选':'来源发布'}${node.pinned?'，已固定':''}`});
      g.append(svgNode('title',{},text(node.row.label)),svgNode('rect',{width:node.width,height:node.height,rx:10}));
      node.lines.forEach((line,i)=>g.append(svgNode('text',{x:12,y:27+i*20},line)));
      g.append(svgNode('text',{x:12,y:node.height-15,class:'focus-node-meta'},`${labels[node.object.type]} · ${node.object.identity==='candidate'?'候选':'来源'}${node.pinned?' · 固定':''}`));
      listen(g,'click',()=>select(node.key,true),drawing.signal);scene.append(g);
    }
    // This is the exact same entry and row collection as the SVG, not another read.
    for(const entry of snapshot.entries){const chosen=entry.key===view.selected;
      const button=control(text(entry.row.label),()=>{select(entry.key);const current=[...list.querySelectorAll('[data-object-key]')].find(n=>n.getAttribute('data-object-key')===entry.key);current?.focus({preventScroll:true});});button.setAttribute('aria-pressed',String(chosen));button.setAttribute('data-object-key',entry.key);
      list.append(el('li',{},button,el('span',{className:'caption'},`${labels[entry.object.type]} · ${entry.object.identity==='candidate'?'候选，非来源事实':'来源发布'} · 修订 ${text(entry.row.revision??'未知')}`)));
    }
    const selected=snapshot.entries.find(e=>e.key===view.selected);
    if(selected){
      detail.append(el('h3',{},text(selected.row.label)),el('p',{},`来源适用性：${({current:'当前',historical:'历史',unknown:'未知',unavailable:'不可用'})[selected.object.source.applicability]||'未知'}；修订 ${text(selected.row.revision??'未知')}`));
      const links=(snapshot.relations||[]).filter(e=>objectKey(e.from)===selected.key||objectKey(e.to)===selected.key);
      detail.append(el('ul',{},...links.map(edge=>{const target=objectKey(edge.from)===selected.key?edge.to:edge.from,entry=snapshot.entries.find(e=>e.key===objectKey(target));return el('li',{},`${objectKey(edge.from)===selected.key?'向外':'来自'} · ${relationText(edge)} → `,control(text(entry?.row.label),()=>select(entry.key)),el('span',{className:'caption'},' · '+text(edge.evidence?.join('；'))));})));
      const ref=selected.object.data?.reference_project_id;if(typeof ref==='string')detail.append(el('p',{},'此对象仅为登记引用；未读取该项目业务。'),control('切换到登记项目',()=>onProject(ref)));
    }else detail.append(el('p',{},'选择图节点或同源列表对象，查看关系与版本。'));
    status.textContent=`${model.nodes.length} / ${snapshot.entries.length} 个对象可见 · ${model.edges.length} 条可见关系 · ${model.crossGroup} 条跨组关系 · ${model.hiddenEdges} 条因折叠或聚焦隐藏。仅视图变化，不保存业务数据。`;
    drawingButtons=false;
    if(bringIntoView)recenter();
    transform();if(focus){const n=[...scene.querySelectorAll('[data-object-key]')].find(n=>n.getAttribute('data-object-key')===view.selected);n?.focus({preventScroll:true});}
  }
  toolbar.append(control('放大',()=>zoom(1.2)),control('缩小',()=>zoom(1/1.2)),control('左移',()=>move(-60,0)),control('右移',()=>move(60,0)),control('上移',()=>move(0,-60)),control('下移',()=>move(0,60)),
    control('重置视图',()=>{view.viewport={x:0,y:0,zoom:1};view.pins=[];view.collapsed=[];draw();emit();}),control('固定 / 释放选中节点',togglePin),groupSelect,el('label',{},related,'只看直接关系'));
  listen(groupSelect,'change',()=>{view.grouping=groupSelect.value;view.collapsed=[];draw();emit();});listen(related,'change',()=>{view.relatedOnly=related.checked;draw();emit();});
  listen(canvas,'keydown',e=>{
    if(['+','=','-','Home'].includes(e.key)){e.preventDefault();if(e.key==='Home'){view.viewport={x:0,y:0,zoom:1};transform();emit();}else zoom(e.key==='-'?1/1.2:1.2);return;}
    if(e.key.toLowerCase()==='p'){e.preventDefault();togglePin();return;}
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();
    const dx=e.key==='ArrowLeft'?-24:e.key==='ArrowRight'?24:0,dy=e.key==='ArrowUp'?-24:e.key==='ArrowDown'?24:0;
    if(e.shiftKey&&view.selected){const n=model.nodes.find(n=>n.key===view.selected);if(n){view.pins=view.pins.filter(p=>p.key!==n.key);view.pins.push({key:n.key,x:Math.max(0,n.x+dx),y:Math.max(0,n.y+dy)});draw(true);emit();}}
    else if(e.ctrlKey||e.metaKey)move(dx,dy);
    else {const i=model.nodes.findIndex(n=>n.key===view.selected),step=dx<0||dy<0?-1:1,n=model.nodes[(i+step+model.nodes.length)%model.nodes.length];if(n)select(n.key,true);}
  });
  const point=e=>{if(canvas.createSVGPoint&&canvas.getScreenCTM()){const p=canvas.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(canvas.getScreenCTM().inverse());}const r=canvas.getBoundingClientRect();return {x:(e.clientX-r.left)*size.width/(r.width||size.width),y:(e.clientY-r.top)*size.height/(r.height||size.height)};};
  function releaseDrag(){const previous=drag;drag=null;if(previous?.started&&(!canvas.hasPointerCapture||canvas.hasPointerCapture(previous.id)))canvas.releasePointerCapture?.(previous.id);return previous;}
  listen(canvas,'pointerdown',e=>{
    if(e.button!==0||drag||e.target.closest?.('[data-group-key]'))return;
    const p=point(e),key=e.target.closest?.('[data-object-key]')?.getAttribute('data-object-key'),n=model.nodes.find(n=>n.key===key);
    drag={id:e.pointerId,p,clientX:e.clientX,clientY:e.clientY,key:n?.key,x:n?.x??view.viewport.x,y:n?.y??view.viewport.y,started:false};
  });
  listen(canvas,'pointermove',e=>{
    if(!drag||drag.id!==e.pointerId)return;
    if(!drag.started){if(Math.hypot(e.clientX-drag.clientX,e.clientY-drag.clientY)<5)return;drag.started=true;canvas.setPointerCapture?.(e.pointerId);}
    e.preventDefault();const p=point(e),dx=p.x-drag.p.x,dy=p.y-drag.p.y;
    if(drag.key){view.pins=view.pins.filter(n=>n.key!==drag.key);view.pins.push({key:drag.key,x:Math.max(0,drag.x+dx/view.viewport.zoom),y:Math.max(0,drag.y+dy/view.viewport.zoom)});view.selected=drag.key;draw();}
    else {view.viewport.x=finite(drag.x+dx,0,-100000,100000);view.viewport.y=finite(drag.y+dy,0,-100000,100000);transform();}
  });
  const end=e=>{if(drag?.id===e.pointerId&&releaseDrag()?.started)emit();};listen(canvas,'pointerup',end);listen(canvas,'pointercancel',end);
  listen(canvas,'lostpointercapture',()=>{if(releaseDrag()?.started)emit();});
  const defs=svgNode('defs'),marker=svgNode('marker',{id:'focus-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svgNode('path',{d:'M 0 0 L 10 5 L 0 10 z'}));defs.append(marker);canvas.append(defs,scene);
  const alternatives=el('details',{},el('summary',{},'同源对象列表 · 不受图分组与折叠影响'),list);
  root.append(el('h2',{},'项目聚焦图'),el('p',{className:'caption'},'图与列表来自同一项目快照；目录卡片属于另一次目录读取。拖动空白平移，拖动节点固定。方向键选节点，Shift＋方向键移动固定，P切换固定，＋/－缩放，Home重置；Tab可离开。'),toolbar,status,canvas,detail,alternatives);
  const unknown=Object.keys(snapshot.unknownTypes||{}).filter(k=>labels[k]);if(unknown.length)root.append(el('p',{className:'caption'},'暂无真实来源对象：'+unknown.map(k=>labels[k]).join('、')+'；不补造目标或运行。'));
  draw();measure();
  if(typeof ResizeObserver==='function'){observer=new ResizeObserver(measure);observer.observe(canvas);}
  else if(typeof window!=='undefined')listen(window,'resize',measure);
  return {element:root,
    focusIdentity(active){if(disposed||!root.contains(active))return null;const target=active?.closest?.('[data-object-key]'),key=target?.getAttribute('data-object-key')||view.selected;
      if(!snapshot.entries.some(e=>e.key===key))return null;return {projectId:snapshot.projectId,projectionVersion:snapshot.projectionVersion,key,surface:list.contains(active)?'list':'svg'};},
    restoreFocus(identity){if(disposed||identity?.projectId!==snapshot.projectId||identity?.projectionVersion!==snapshot.projectionVersion||!snapshot.entries.some(e=>e.key===identity.key)||!['list','svg'].includes(identity.surface))return false;
      select(identity.key);if(identity.surface==='list')alternatives.open=true;const container=identity.surface==='list'?list:scene;
      const target=[...container.querySelectorAll('[data-object-key]')].find(n=>n.getAttribute('data-object-key')===identity.key);target?.focus({preventScroll:true});return !!target;},
    state:()=>graphViewState(snapshot,view),select,dispose(){releaseDrag();disposed=true;observer?.disconnect();observer=null;lifetime.abort();drawing.abort();drag=null;model=null;view=null;root.replaceChildren();snapshot=null;}};
}

// Task-local builder: Root submits <=10 nodes from one section per call.
// CFG, MAP, PAGE and OPS are injected from this unit's JSON; no network or credentials.
const made=[], changed=[], next={};
await Promise.all([{family:'Noto Sans SC',style:'Regular'},{family:'Noto Sans SC',style:'Medium'},{family:'Noto Sans SC',style:'Bold'},{family:'Noto Serif SC',style:'Medium'},{family:'Inter',style:'Regular'}].map(f=>figma.loadFontAsync(f)));
const page=await figma.getNodeByIdAsync(CFG.pages[PAGE]);
await figma.setCurrentPageAsync(page);
const vars={};
await Promise.all(Object.entries({...CFG.semanticVars,radius:CFG.typography.radius}).map(async ([k,v])=>{vars[k]=await figma.variables.getVariableByIdAsync(v);}));
const spaces=[...new Set(OPS.flatMap(o=>[o.pad,o.gap]).filter(Boolean))];
await Promise.all(spaces.map(async n=>{vars['space'+n]=await figma.variables.importVariableByKeyAsync(CFG.spacingKeys[n]);}));
const paint=k=>figma.variables.setBoundVariableForPaint({type:'SOLID',color:{r:0,g:0,b:0}},'color',vars[k]);
const buttons=OPS.some(o=>o.type==='button')?await figma.importComponentSetByKeyAsync('cc8b558dc7d9684011b6b99ce8e6509399bc836b'):null;
for(const op of OPS){
 if(MAP[op.key])throw new Error('Already created '+op.key);
 const parent=op.parent?await figma.getNodeByIdAsync(next[op.parent]||MAP[op.parent]):page;
 if(!parent)throw new Error('Missing parent '+op.parent);
 let n;
 if(op.type==='text'){
  n=figma.createText();n.resize(op.w||300,24);
  await n.setTextStyleIdAsync(CFG.typography.styles[op.style||'Body']);
  n.characters=op.text;n.fills=[paint(op.color||'text')];
  if(op.fontScale){n.fontSize=n.fontSize*op.fontScale;if(n.lineHeight.unit==='PIXELS')n.lineHeight={unit:'PIXELS',value:n.lineHeight.value*op.fontScale};}
 }else if(op.type==='button'){
  const variant=op.variant==='secondary'?'Neutral':op.variant==='quiet'?'Subtle':'Primary';
  const state=op.variant==='disabled'?'Disabled':'Default';
  const comp=buttons.children.find(c=>c.name===`Variant=${variant}, State=${state}, Size=Medium`);
  n=comp.createInstance();n.setProperties({'Label#2:0':op.text,'Has Icon End#4:64':false,'Has Icon Start#4:128':false});
  for(const t of n.findAllWithCriteria({types:['TEXT']})){await t.setTextStyleIdAsync(CFG.typography.styles.Label);if(op.fontScale){t.fontSize=t.fontSize*op.fontScale;t.lineHeight={unit:'PIXELS',value:44};}t.fills=[paint(!op.variant||op.variant==='primary'?'onaccent':op.variant==='disabled'?'muted':'text')];changed.push(t.id);}
  n.fills=op.variant==='quiet'?[]:[paint(!op.variant||op.variant==='primary'?'accent':op.variant==='disabled'?'canvas':'surface')];
  n.strokes=op.variant==='secondary'?[paint('border')]:[];n.resize(op.w||Math.max(92,op.text.length*14+32),44*(op.fontScale||1));
  for(const k of ['topLeftRadius','topRightRadius','bottomLeftRadius','bottomRightRadius'])n.setBoundVariable(k,vars.radius);
 }else if(op.type==='preview'){
  const source=await figma.getNodeByIdAsync(MAP[op.source]);n=source.clone();n.rescale(op.w/source.width);if(op.source.endsWith('/mobile')){n.primaryAxisSizingMode='AUTO';n.overflowDirection='NONE';}made.push(...n.findAll(()=>true).map(c=>c.id));
 }else if(op.type==='instance'){
  const comp=await figma.getNodeByIdAsync(next[op.component]||MAP[op.component]);n=comp.createInstance();
  const props={};for(const [k,v]of Object.entries(op.values||{})){const key=Object.keys(n.componentProperties).find(p=>p.split('#')[0]===k);if(!key)throw new Error('Missing property '+k);props[key]=v;}
  n.setProperties(props);if(op.w)n.resize(op.w,op.h||n.height);
 }else{
  n=op.type==='component'?figma.createComponent():figma.createAutoLayout(op.axis||'VERTICAL');n.layoutMode=op.axis||'VERTICAL';
  n.resize(op.w||100,op.h||100);
  n.primaryAxisSizingMode=(op.axis==='HORIZONTAL'?op.w:op.h)?'FIXED':'AUTO';
  n.counterAxisSizingMode=(op.axis==='HORIZONTAL'?op.h:op.w)?'FIXED':'AUTO';
  n.fills=op.fill?[paint(op.fill)]:[];n.clipsContent=!!op.clip;
  if(op.stroke){n.strokes=[paint(op.stroke)];n.strokeWeight=1;}
  if(op.radius)for(const k of ['topLeftRadius','topRightRadius','bottomLeftRadius','bottomRightRadius'])n.setBoundVariable(k,vars.radius);
  if(op.pad)for(const k of ['paddingLeft','paddingRight','paddingTop','paddingBottom'])n.setBoundVariable(k,vars['space'+op.pad]);
  if(op.gap)n.setBoundVariable('itemSpacing',vars['space'+op.gap]);
  if(op.center)n.counterAxisAlignItems='CENTER';if(op.between)n.primaryAxisAlignItems='SPACE_BETWEEN';
  if(op.scroll)n.overflowDirection='VERTICAL';
  if(op.description&&op.type==='component')n.description=op.description;
 }
 n.name=op.key;parent.appendChild(n);if(op.index!==undefined)parent.insertChild(op.index,n);
 if(op.type==='text'){n.textAutoResize='HEIGHT';n.characters=op.text;}
 if(op.x!==undefined)n.x=op.x;if(op.y!==undefined)n.y=op.y;
 if(op.fillWidth)n.layoutSizingHorizontal='FILL';if(op.fillHeight)n.layoutSizingVertical='FILL';
 if(op.prop){const k=parent.addComponentProperty(op.prop,'TEXT',op.text);n.componentPropertyReferences={characters:k};}
 if(op.focus){n.strokes=[paint('accent')];n.strokeWeight=2;}
 if(op.direction){n.setExplicitVariableModeForCollection(CFG.collections.theme,CFG.modes[op.direction]);n.setExplicitVariableModeForCollection(CFG.typography.shapeCollection,CFG.shapeModes[op.direction]);}
 made.push(n.id);next[op.key]=n.id;
}
return {createdNodeIds:made,mutatedNodeIds:changed,map:next};

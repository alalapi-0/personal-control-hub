function sha256(s){
 const b=unescape(encodeURIComponent(s)),w=[],h=[1779033703,3144134277,1013904242,2773480762,1359893119,2600822924,528734635,1541459225];
 const k=[1116352408,1899447441,3049323471,3921009573,961987163,1508970993,2453635748,2870763221,3624381080,310598401,607225278,1426881987,1925078388,2162078206,2614888103,3248222580,3835390401,4022224774,264347078,604807628,770255983,1249150122,1555081692,1996064986,2554220882,2821834349,2952996808,3210313671,3336571891,3584528711,113926993,338241895,666307205,773529912,1294757372,1396182291,1695183700,1986661051,2177026350,2456956037,2730485921,2820302411,3259730800,3345764771,3516065817,3600352804,4094571909,275423344,430227734,506948616,659060556,883997877,958139571,1322822218,1537002063,1747873779,1955562222,2024104815,2227730452,2361852424,2428436474,2756734187,3204031479,3329325298];
 const r=(x,n)=>(x>>>n)|(x<<(32-n));
 for(let i=0;i<b.length;i++)w[i>>2]=(w[i>>2]||0)|(b.charCodeAt(i)<<((3-i%4)*8));
 w[b.length>>2]=(w[b.length>>2]||0)|(128<<((3-b.length%4)*8));
 const end=(((b.length+8)>>6)+1)*16;w[end-2]=Math.floor(b.length*8/4294967296);w[end-1]=b.length*8;
 for(let o=0;o<end;o+=16){
  const v=[];for(let i=0;i<64;i++)v[i]=i<16?(w[o+i]||0):(((r(v[i-15],7)^r(v[i-15],18)^(v[i-15]>>>3))+v[i-16]+(r(v[i-2],17)^r(v[i-2],19)^(v[i-2]>>>10))+v[i-7])|0);
  let [a,c,d,e,f,g,j,l]=h;
  for(let i=0;i<64;i++){const t1=(l+(r(f,6)^r(f,11)^r(f,25))+((f&g)^((~f)&j))+k[i]+v[i])|0,t2=((r(a,2)^r(a,13)^r(a,22))+((a&c)^(a&d)^(c&d)))|0;l=j;j=g;g=f;f=(e+t1)|0;e=d;d=c;c=a;a=(t1+t2)|0;}
  const v2=[a,c,d,e,f,g,j,l];for(let i=0;i<8;i++)h[i]=(h[i]+v2[i])|0;
 }
 return h.map(x=>(x>>>0).toString(16).padStart(8,'0')).join('');
}


const fields=['id','name','type','visible','x','y','width','height','opacity','rotation','fills','strokes','strokeWeight','strokeAlign','effects','cornerRadius','topLeftRadius','topRightRadius','bottomLeftRadius','bottomRightRadius','clipsContent','overflowDirection','layoutMode','primaryAxisSizingMode','counterAxisSizingMode','primaryAxisAlignItems','counterAxisAlignItems','paddingLeft','paddingRight','paddingTop','paddingBottom','itemSpacing','layoutSizingHorizontal','layoutSizingVertical','boundVariables','explicitVariableModes','componentProperties','componentPropertyDefinitions','characters','fontName','fontSize','fontWeight','lineHeight','letterSpacing','textAlignHorizontal','textAlignVertical','textAutoResize','textStyleId','reactions'];
function snapshot(n){
 const o={};for(const k of fields){if(k in n){const v=n[k];if(v!==undefined)o[k]=typeof v==='symbol'?'MIXED':JSON.parse(JSON.stringify(v));}}
 if('children' in n && n.visible!==false)o.children=n.children.map(snapshot);
 return o;
}


function structure(node){
 const raw=snapshot(node),paths={};
 function index(o,path){paths[o.id]=path;(o.children||[]).forEach((c,i)=>index(c,path+'/'+i));}index(raw,'root');
 function norm(v,key,root){
  if(Array.isArray(v))return v.map(x=>norm(x,key,false));
  if(typeof v==='number'&&['x','y','width','height'].includes(key))return Math.round(v*1000)/1000;
  if(!v||typeof v!=='object')return key==='destinationId'?(paths[v]||v):v;
  const o={};for(const[k,x]of Object.entries(v)){
   if(k==='id'||(root&&['name','x','y'].includes(k)))continue;
   if(key==='boundVariables'&&['fills','strokes','color'].includes(k))continue;
   if(k==='color'&&(key==='fills'||key==='strokes'||key==='effects'))continue;
   if(k==='boundVariables'&&(key==='fills'||key==='strokes'))continue;
   o[k]=norm(x,k,false);
  }return o;
 }
 return norm(raw,'',true);
}

const page=await figma.getNodeByIdAsync('2:4');await figma.setCurrentPageAsync(page);
await Promise.all(FONTS.map(f=>figma.loadFontAsync(f)));
const target=await figma.getNodeByIdAsync(TARGET),oldRoles=OLD_ROLES;
const vars={};for(const[role,ids]of Object.entries(PALETTE.variables))vars[role]=await figma.variables.getVariableByIdAsync(ids.semantic);
const results=[],createdNodeIds=[];
for(let i=0;i<SOURCES.length;i++){
 const src=await figma.getNodeByIdAsync(SOURCES[i].id),before=sha256(JSON.stringify(structure(src))),clone=src.clone();
 target.appendChild(clone);clone.name=PALETTE.id+'/'+SOURCES[i].key.slice(2);clone.x=100+INDEX*2050;clone.y=100+i*1080;
 let count=0;const unknown=[];function paint(n,visible){
  createdNodeIds.push(n.id);visible=visible&&n.visible!==false;
  if(visible)for(const field of ['fills','strokes'])if(field in n&&Array.isArray(n[field]))n[field]=n[field].map(p=>{
   if(p.type!=='SOLID'||p.visible===false)return p;
   const role=oldRoles[p.boundVariables?.color?.id];if(!role){unknown.push({id:n.id,field});return p;}
   count++;return figma.variables.setBoundVariableForPaint({...p},'color',vars[role]);
  });
  if('children'in n)n.children.forEach(c=>paint(c,visible));
 }paint(clone,true);
 const after=sha256(JSON.stringify(structure(clone)));
 if(before!==after)throw Error('Non-color structure drift: '+SOURCES[i].key+' '+before+' '+after);
 if(unknown.length)throw Error('Unmapped visible paints '+JSON.stringify(unknown));
 results.push({palette:PALETTE.id,key:clone.name,id:clone.id,sourceId:src.id,width:clone.width,height:clone.height,structure_sha256:after,source_structure_sha256:before,content_sha256:sha256(JSON.stringify(snapshot(clone))),paintsChanged:count,statesGrid:('children'in clone?clone.children.find(n=>n.name.endsWith('/grid'))?.id:null)});
}
return {frames:results,createdNodeIds};

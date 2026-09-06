
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

const p=await figma.getNodeByIdAsync(PAGE);await figma.setCurrentPageAsync(p);
const summaries=[],allNodes=new Map();
function inspect(n,root){
 const stats={nodes:0,texts:0,instances:0,imageFills:0,scrolls:[],horizontalErrors:[],verticalErrors:[],textMinimum:999,reactions:0};
 function walk(q,preview,visible){
  allNodes.set(q.id,q);visible=visible&&q.visible!==false;
  if(!visible)return;
  preview=preview||q.name.endsWith('/candidate/preview');
  stats.nodes++;
  if(q.type==='TEXT'){stats.texts++;if(!preview&&typeof q.fontSize==='number')stats.textMinimum=Math.min(stats.textMinimum,q.fontSize);}
  if(q.type==='INSTANCE')stats.instances++;
  if('fills' in q && Array.isArray(q.fills))stats.imageFills+=q.fills.filter(f=>f.type==='IMAGE').length;
  const overflow='overflowDirection' in q?q.overflowDirection:'NONE';if(overflow!=='NONE')stats.scrolls.push({id:q.id,direction:overflow,h:q.height});
  if('reactions' in q && q.reactions?.length)stats.reactions+=q.reactions.length;
  for(const c of ('children' in q?q.children:[])){
   if(c.visible!==false){
    if(c.x < -1.1 || c.x+c.width>q.width+1.1)stats.horizontalErrors.push({id:c.id,parent:q.id,name:c.name,x:c.x,w:c.width,pw:q.width});
    if(c.y < -1.1 || (c.y+c.height>q.height+1.1&&overflow==='NONE'))stats.verticalErrors.push({id:c.id,parent:q.id,name:c.name,y:c.y,h:c.height,ph:q.height});
   }
   walk(c,preview,visible);
  }
 }
 walk(n,false,true);return stats;
}
for(const [key,id] of Object.entries(ROOTS)){
 const n=await figma.getNodeByIdAsync(id);if(!n)throw Error('Missing '+key);
 const data=snapshot(n),json=JSON.stringify(data),stats=inspect(n,key);
 summaries.push({key,id,name:n.name,type:n.type,width:n.width,height:n.height,contentBytes:unescape(encodeURIComponent(json)).length,sha256:sha256(json),...stats,horizontalErrorCount:stats.horizontalErrors.length,verticalErrorCount:stats.verticalErrors.length,horizontalErrors:stats.horizontalErrors.slice(0,6),verticalErrors:stats.verticalErrors.slice(0,6)});
}
const mismatches=[];
for(const o of EXPECTED||[]){
 const n=allNodes.get(o.id);if(!n){mismatches.push({key:o.key,error:'missing'});continue;}
 if(o.type==='text'&&n.characters!==o.text)mismatches.push({key:o.key,error:'text_mismatch'});
 if(o.type==='instance')for(const [k,v]of Object.entries(o.values||{})){
  const actual=Object.entries(n.componentProperties||{}).find(([name])=>name.split('#')[0]===k)?.[1]?.value;
  if(actual!==v)mismatches.push({key:o.key,error:'property_mismatch',property:k,actual,expected:v});
 }
 if(o.type==='button'&&(n.height<44-0.1||n.width<44-0.1))mismatches.push({key:o.key,error:'target_too_small',w:n.width,h:n.height});
 if(o.fontScale&&o.type==='text'&&n.fontSize!==28)mismatches.push({key:o.key,error:'scale_mismatch',actual:n.fontSize});
 if(o.focus&&(n.strokeWeight!==2||!n.strokes?.length))mismatches.push({key:o.key,error:'focus_missing'});
}
return {schema:'hub-figma-semantic-v1',identity:'SHA-256 of rendered-node snapshot including bindings and reactions; hidden subtrees omitted; property order defined by readback-script.js',page:{id:p.id,name:p.name,childIds:p.children.map(n=>n.id)},summaries,expectedCount:EXPECTED.length,mismatchCount:mismatches.length,mismatches:mismatches.slice(0,15)};

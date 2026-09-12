
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



if(sha256('abc')!=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')throw Error('SHA256 vector');
const ids=['11:2','25:14','13:13','17:10','11:79','25:102','32:60'];
const scenes=[];
for(const id of ids){const n=await figma.getNodeByIdAsync(id);const raw=snapshot(n);const all=[n,...n.findAll()];const texts=n.findAllWithCriteria({types:['TEXT']});scenes.push({id,name:n.name,width:n.width,height:n.height,content_sha256:sha256(JSON.stringify(raw)),nodes:all.length,texts:texts.length,instances:all.filter(x=>x.type==='INSTANCE').length,auto_layouts:all.filter(x=>x.layoutMode&&x.layoutMode!=='NONE').length,fonts:[...new Set(texts.map(t=>JSON.stringify(t.fontName)))],task_title_present:texts.some(t=>t.characters==='认识终端'),task_action_present:texts.some(t=>t.characters==='进入当前任务'),zero_progress_present:texts.some(t=>t.characters==='0 / 3')});}
const variables=(await figma.variables.getLocalVariablesAsync()).map(v=>({id:v.id,name:v.name,type:v.resolvedType,scopes:v.scopes,valuesByMode:v.valuesByMode}));
const collections=(await figma.variables.getLocalVariableCollectionsAsync()).map(c=>({id:c.id,name:c.name,modes:c.modes}));
return {file_key:'qHIMgQnOulj5TEEYk9Yaod',page_id:'0:1',sha256_self_test:true,scenes,variables,collections,context_sha256:sha256(JSON.stringify({variables,collections})),capture_refs_removed:await Promise.all(['2:2','18:10','24:14'].map(async id=>({id,absent:(await figma.getNodeByIdAsync(id))===null})))};

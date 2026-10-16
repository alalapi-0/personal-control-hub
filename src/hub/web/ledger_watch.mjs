// Observe the existing ledger; this never schedules or starts source collection.
export function validLedgerHead(head){
  return head===null||Boolean(head&&Number.isSafeInteger(head.sequence)&&head.sequence>=0
    &&typeof head.hash==='string'&&/^[a-f0-9]{64}$/.test(head.hash)
    &&(head.sequence===0)===(head.hash==='0'.repeat(64)));
}
const key=head=>head===null?'none':`${head.sequence}:${head.hash}`;

export function watchLedger({read,reconcile,epoch,visible,online,onStatus=()=>{},
  schedule=setTimeout,cancel=clearTimeout,now=Date.now,interval=5000,hiddenInterval=30000,freshnessInterval=60000}){
  let timer,stopped=false,running=false,again=false,known=false,headKey,backend,lastApplied=now();
  function accept(head){if(!validLedgerHead(head))throw new Error('INVALID_LEDGER_HEAD');known=true;headKey=key(head);lastApplied=now();}
  function later(){cancel(timer);if(!stopped)timer=schedule(tick,visible()?interval:hiddenInterval);}
  async function tick(){
    if(stopped)return;
    if(running){again=true;return;}
    cancel(timer);
    if(!visible()||!online()){later();return;}
    running=true;const version=epoch();
    try{
      const value=await read();
      if(stopped||version!==epoch()||!visible())return;
      if(!validLedgerHead(value?.head)||typeof value?.backend_instance!=='string'
        ||!/^[a-f0-9]{32}$/.test(value.backend_instance))throw new Error('INVALID_LEDGER_HEAD_RESPONSE');
      const changed=known&&(key(value.head)!==headKey||backend!==undefined&&backend!==value.backend_instance
        ||now()-lastApplied>=freshnessInterval);
      if(changed){
        const applied=await reconcile();
        if(stopped||version!==epoch())return;
        // The projection may be newer than the observed head. A failed or stale
        // read is not acknowledged, so the next ordinary observation retries.
        if(applied===undefined)return;
        accept(applied);onStatus('updated');
      }else if(!known)accept(value.head);
      backend=value.backend_instance;onStatus('ready');
    }catch(error){if(!stopped&&version===epoch())onStatus('unavailable',error);}
    finally{running=false;if(again&&!stopped){again=false;timer=schedule(tick,0);}else later();}
  }
  later();
  return {accept,wake:tick,stop(){stopped=true;cancel(timer);}};
}

// Keep the actual editor nodes and their closure state during source updates.
// Recreating input values alone would lose pending receipts and CAS versions.
export function replaceProjection(container,next,{preserve=true}={}){
  const active=document.activeElement,x=scrollX,y=scrollY;
  const id=active?.id,href=active?.closest?.('a')?.getAttribute('href');
  const open=[...container.querySelectorAll('details')].filter(n=>n.open)
    .map(n=>n.querySelector('summary')?.textContent);
  if(preserve)for(const previous of container.querySelectorAll('[data-live-preserve]')){
    const target=[...next.querySelectorAll('[data-live-preserve]')]
      .find(n=>n.dataset.livePreserve===previous.dataset.livePreserve);
    if(target)target.replaceWith(previous);
  }
  container.replaceChildren(next);
  for(const details of container.querySelectorAll('details'))
    if(open.includes(details.querySelector('summary')?.textContent))details.open=true;
  const focus=active?.isConnected?active:id?document.getElementById(id):href?
    [...container.querySelectorAll('a')].find(n=>n.getAttribute('href')===href):null;
  focus?.focus({preventScroll:true});window.scrollTo(x,y);
}

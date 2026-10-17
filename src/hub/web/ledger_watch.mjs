// Observe the existing ledger; this never schedules or starts source collection.
export function validLedgerHead(head){
  return head===null||Boolean(head&&Number.isSafeInteger(head.sequence)&&head.sequence>=0
    &&typeof head.hash==='string'&&/^[a-f0-9]{64}$/.test(head.hash)
    &&(head.sequence===0)===(head.hash==='0'.repeat(64)));
}

export function watchLedger({read,reconcile,epoch,visible,online,onStatus=()=>{}}){
  let stopped=false,running=false,known=false;
  function accept(head){if(!validLedgerHead(head))throw new Error('INVALID_LEDGER_HEAD');known=true;}
  async function wake(){
    // No periodic head/projection timer: only explicit or visibility/network
    // events wake this observer, and concurrent events share one bounded read.
    if(stopped||running||!visible()||!online())return;
    running=true;const version=epoch();
    try{
      const value=await read();
      if(stopped||version!==epoch()||!visible()||!online())return;
      if(!validLedgerHead(value?.head)||typeof value?.backend_instance!=='string'
        ||!/^[a-f0-9]{32}$/.test(value.backend_instance))throw new Error('INVALID_LEDGER_HEAD_RESPONSE');
      // An explicit wake also reprojects unchanged heads: source age and Hub
      // management configuration can change without an appended ledger result.
      const applied=await reconcile();
      if(stopped||version!==epoch())return;
      if(applied!==undefined){accept(applied);onStatus('updated');}
      else if(!known)accept(value.head);
      onStatus('ready');
    }catch(error){if(!stopped&&version===epoch())onStatus('unavailable',error);}
    finally{running=false;}
  }
  return {accept,wake,stop(){stopped=true;}};
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

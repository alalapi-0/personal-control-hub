import test from 'node:test';
import assert from 'node:assert/strict';
import {watchLedger,validLedgerHead} from '../src/hub/web/ledger_watch.mjs';
const head=n=>({sequence:n,hash:String(n).repeat(64)});
const instance='a'.repeat(32);
function fixture(){
  const state={version:1,visible:true,online:true,time:0,value:{head:head(1),backend_instance:instance},reads:0,renders:0,status:[],timers:new Map()};
  let timerId=0;
  const watcher=watchLedger({
    read:async()=>{state.reads++;return state.value;},
    reconcile:async()=>{state.renders++;return state.value.head;},epoch:()=>state.version,
    visible:()=>state.visible,online:()=>state.online,now:()=>state.time,
    onStatus:value=>state.status.push(value),
    schedule:(fn,ms)=>{state.timers.set(++timerId,{fn,ms});return timerId;},cancel:id=>state.timers.delete(id),
  });
  return {state,watcher};
}
test('ordinary 5s head observations never refresh an unchanged projection, but real changes do',async()=>{
  const {state,watcher}=fixture();watcher.accept(head(1));
  assert.equal([...state.timers.values()][0].ms,5000);
  await watcher.wake();assert.equal(state.reads,1);assert.equal(state.renders,0);
  state.value.head=head(2);await watcher.wake();assert.equal(state.renders,1);
  await watcher.wake();assert.equal(state.renders,1);assert.ok(state.status.includes('updated'));
  state.time=60000;await watcher.wake();assert.equal(state.renders,2,'unchanged ledger still ages source freshness');
  watcher.stop();assert.equal(state.timers.size,0);
});
test('hidden/offline pages do not collect or reconcile, and wake resumes immediately',async()=>{
  const {state,watcher}=fixture();watcher.accept(head(1));state.visible=false;
  await watcher.wake();assert.equal(state.reads,0);assert.equal([...state.timers.values()][0].ms,30000);
  state.visible=true;state.online=false;await watcher.wake();assert.equal(state.reads,0);
  state.online=true;state.value.head=head(2);await watcher.wake();assert.equal(state.renders,1);
  watcher.stop();
});
test('a late head observation from another route cannot apply there',async()=>{
  let release,version=1,renders=0;
  const watcher=watchLedger({read:()=>new Promise(resolve=>{release=resolve;}),reconcile:async()=>{renders++;return head(2);},
    epoch:()=>version,visible:()=>true,online:()=>true,schedule:()=>1,cancel:()=>{}});
  watcher.accept(head(1));const pending=watcher.wake();version++;
  release({head:head(2),backend_instance:instance});await pending;assert.equal(renders,0);watcher.stop();
});
test('failed projection keeps old acknowledgement, and a newer atomic projection is accepted',async()=>{
  let fail=true,renders=0;const status=[];
  const watcher=watchLedger({read:async()=>({head:head(2),backend_instance:instance}),
    reconcile:async()=>{renders++;if(fail)throw new Error('read unavailable');return head(3);},
    epoch:()=>1,visible:()=>true,online:()=>true,onStatus:s=>status.push(s),schedule:()=>1,cancel:()=>{}});
  watcher.accept(head(1));await watcher.wake();assert.equal(renders,1);assert.ok(status.includes('unavailable'));
  fail=false;await watcher.wake();assert.equal(renders,2);
  watcher.accept(head(2));watcher.stop();
});
test('bad head payloads and backend identity cannot confer successful reconciliation',async()=>{
  for(const value of [{head:{sequence:-1,hash:'b'.repeat(64)},backend_instance:instance},{head:head(2),backend_instance:'private-path'},{}]){
    const {state,watcher}=fixture();watcher.accept(head(1));state.value=value;
    await watcher.wake();assert.equal(state.renders,0);assert.ok(state.status.includes('unavailable'));watcher.stop();
  }
  assert.equal(validLedgerHead(null),true);assert.equal(validLedgerHead({sequence:2,hash:'b'.repeat(64)}),true);
  assert.equal(validLedgerHead({sequence:2,hash:'bad'}),false);
});

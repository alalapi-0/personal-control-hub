import assert from 'node:assert/strict';
import test from 'node:test';
import {controlState} from '../src/hub/web/tasks.js';

const task={status:'waiting_approval',thread_id:'thread',turn_id:'turn'};
const control={kind:'approval',status:'pending',version:1,expires_at:20,
  thread_id:'thread',turn_id:'turn',allowed_decisions:['decline','accept']};

test('single request approval requires owner and frozen allowed decision',()=>{
  assert.equal(controlState(control,task,true,10).canAccept,true);
  assert.equal(controlState(control,task,true,10).canDecline,true);
  assert.equal(controlState(control,task,false,10).canAccept,false);
  assert.equal(controlState(control,task,false,10).canDecline,false);
  for(const patch of [{kind:'input'},{allowed_decisions:['decline']},{version:2},
    {thread_id:'other'},{turn_id:'other'},{status:'accepted'},{status:'declined'}])
    assert.equal(controlState({...control,...patch},task,true,10).canAccept,false);
});

test('expired and reconciliating requests cannot permit an operation',()=>{
  assert.equal(controlState(control,task,true,20).canAccept,false);
  assert.equal(controlState(control,task,true,20).label,'已过期');
  for(const status of ['requires_reconcile','cancelled','checks_complete','failed']){
    assert.equal(controlState(control,{...task,status},true,10).canAccept,false);
    assert.equal(controlState(control,{...task,status},true,10).canDecline,false);
  }
});

test('accepted request has a single-request label rather than result acceptance',()=>{
  assert.equal(controlState({...control,status:'accepted'},task,true,10).label,'仅本次已允许');
  assert.equal(controlState({...control,status:'declined'},task,true,10).label,'已拒绝');
});

test('input answers require the exact pending request, owner, and active turn',()=>{
  const input={...control,kind:'input',allowed_decisions:['decline','answer']};
  const waiting={...task,status:'waiting_input'};
  assert.equal(controlState(input,waiting,true,10).canAnswer,true);
  assert.equal(controlState(input,waiting,false,10).canAnswer,false);
  for(const patch of [{thread_id:'other'},{turn_id:'old'},{version:2},{status:'answered'},{allowed_decisions:['decline']}])
    assert.equal(controlState({...input,...patch},waiting,true,10).canAnswer,false);
  assert.equal(controlState(input,waiting,true,20).canAnswer,false);
  assert.equal(controlState(input,{...waiting,status:'cancelled'},true,10).canAnswer,false);
  assert.equal(controlState({...input,status:'answered'},waiting,true,10).label,'已提交补充输入');
});

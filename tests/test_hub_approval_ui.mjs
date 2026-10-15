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

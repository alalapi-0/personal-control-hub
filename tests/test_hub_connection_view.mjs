import assert from 'node:assert/strict';
import test from 'node:test';

import {attention, freshness, refreshable} from '../src/hub/web/connection_view.mjs';

function project(overrides = {}) {
  return {
    declared: {
      enabled: true,
      summary_enabled: true,
      connection_read_allowed: true,
      hub_connection_exception: null,
      legacy_hub_connection_exception: null,
      ...overrides.declared,
    },
    operational: {latest_attempt: null, last_success: null, ...overrides.operational},
    freshness: {
      state: 'unknown',
      authority_drift: false,
      local_presence: 'registered_local',
      ...overrides.freshness,
    },
    errors: overrides.errors ?? [],
  };
}

test('actual-registry-shaped manga keeps current failure actionable and refreshable', () => {
  const manga = project({
    declared: {
      enabled: false,
      summary_enabled: true,
      connection_read_allowed: true,
      hub_connection_exception: null,
      legacy_hub_connection_exception: {status: 'AUTHORIZED_EXCEPTION'},
    },
    operational: {
      latest_attempt: {success: false, disposition: 'missing_declaration'},
      last_success: {success: true, disposition: 'resolved'},
    },
    errors: [{code: 'missing_declaration'}],
  });
  assert.equal(attention(manga), true);
  assert.equal(freshness(manga), '最新读取失败');
  assert.equal(refreshable(manga), true);
});

test('latest failure wins over current exception and historical success', () => {
  const failed = project({
    declared: {hub_connection_exception: {status: 'AUTHORIZED_EXCEPTION'}},
    operational: {
      latest_attempt: {success: false, disposition: 'invalid'},
      last_success: {success: true, disposition: 'resolved'},
    },
    freshness: {state: 'stale'},
    errors: [{code: 'invalid'}],
  });
  assert.equal(attention(failed), true);
  assert.equal(freshness(failed), '最新读取失败');
});

test('explicit current permission controls refresh without enabled fallback', () => {
  assert.equal(refreshable(project({
    declared: {enabled: true, connection_read_allowed: false},
  })), false);
  assert.equal(refreshable(project({
    declared: {enabled: true, connection_read_allowed: undefined},
  })), false);
});

test('removed project has its terminal label and no action or attention', () => {
  const removed = project({
    freshness: {state: 'removed_local', local_presence: 'removed_local'},
    operational: {latest_attempt: {success: false, disposition: 'removed_local'}},
    errors: [{code: 'removed_local'}],
  });
  assert.equal(freshness(removed), '已从本地移除');
  assert.equal(attention(removed), false);
  assert.equal(refreshable(removed), false);
});

test('all 23 missing declarations remain unknown failures', () => {
  const missing = Array.from({length: 23}, (_, index) => project({
    declared: {hub_connection_exception: index % 2 ? null : {status: 'AUTHORIZED_EXCEPTION'}},
    operational: {latest_attempt: {success: false, disposition: 'missing_declaration'}},
    freshness: {state: 'unknown'},
    errors: [{code: 'missing_declaration'}],
  }));
  assert.equal(missing.every(item => item.freshness.state === 'unknown'), true);
  assert.equal(missing.every(item => attention(item)), true);
  assert.equal(missing.every(item => freshness(item) === '最新读取失败'), true);
});

test('normal fresh, stale, unknown, drift, and exception labels remain clear', () => {
  assert.deepEqual([
    freshness(project({freshness: {state: 'fresh'}})),
    freshness(project({freshness: {state: 'stale'}})),
    freshness(project()),
    freshness(project({freshness: {state: 'stale', authority_drift: true}})),
    freshness(project({declared: {hub_connection_exception: {status: 'AUTHORIZED_EXCEPTION'}}})),
  ], ['已更新', '上次快照 · 尚未更新', '尚无成功快照',
      '来源规则变化 · 需重新核对', '已登记例外']);
});

test('helpers do not mutate the project DTO', () => {
  const value = project({
    operational: {latest_attempt: {success: false, disposition: 'invalid'}},
    errors: [{code: 'invalid'}],
  });
  const before = JSON.stringify(value);
  attention(value);
  freshness(value);
  refreshable(value);
  assert.equal(JSON.stringify(value), before);
});

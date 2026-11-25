import { describe, expect, it } from 'vitest';
import {
  authorNodeId,
  instanceStatusI18nKey,
  instanceStatusNoticeVariant,
  isFailingNodeState,
  isTerminalInstanceStatus,
  NODE_STATE_STROKE,
  NODE_STATE_TEXT,
  nodeStateI18nKey,
  nodeStateMap,
} from './run-overlay';

describe('run-overlay 状态映射（与 runtime-inspector STATE_BADGE 色相同族）', () => {
  it('节点态描边/文字色覆盖 NodeState 九值词表', () => {
    // 九值全有映射（未登记态回退 undefined 由调用方走默认描边）。
    for (const state of ['PENDING', 'READY', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'SKIPPED', 'CANCELLED', 'STALE']) {
      expect(NODE_STATE_STROKE[state], state).toBeTruthy();
      expect(NODE_STATE_TEXT[state], state).toBeTruthy();
    }
    expect(NODE_STATE_STROKE.SUCCEEDED).toContain('stroke-status-success');
    expect(NODE_STATE_STROKE.RUNNING).toContain('stroke-status-info');
    expect(NODE_STATE_STROKE.FAILED).toContain('stroke-status-critical');
    expect(NODE_STATE_STROKE.BLOCKED).toContain('stroke-status-warning');
    expect(NODE_STATE_TEXT.SUCCEEDED).toContain('text-status-success');
  });

  it('wire id → 作者 id 映射（transform 前缀 rsplit；其余恒等）', () => {
    expect(authorNodeId('transform:buffer:buffered')).toBe('buffered');
    expect(authorNodeId('poi_in')).toBe('poi_in');
    expect(nodeStateMap([
      { node_id: 'poi_in', state: 'SUCCEEDED' },
      { node_id: 'transform:buffer:buffered', state: 'RUNNING' },
    ])).toEqual({ poi_in: 'SUCCEEDED', buffered: 'RUNNING' });
  });

  it('实例终态判定（轮询停止条件）与 notice variant', () => {
    expect(isTerminalInstanceStatus('succeeded')).toBe(true);
    expect(isTerminalInstanceStatus('failed')).toBe(true);
    expect(isTerminalInstanceStatus('cancelled')).toBe(true);
    expect(isTerminalInstanceStatus('superseded')).toBe(true);
    expect(isTerminalInstanceStatus('running')).toBe(false);
    expect(instanceStatusNoticeVariant('succeeded')).toBe('success');
    expect(instanceStatusNoticeVariant('failed')).toBe('error');
    expect(instanceStatusNoticeVariant('running')).toBe('info');
  });

  it('i18n 键覆盖已登记词表；未知态回退空串（调用方原样展示）', () => {
    expect(nodeStateI18nKey('RUNNING')).toBe('run.statusRunning');
    expect(nodeStateI18nKey('STALE')).toBe('run.statusStale');
    expect(nodeStateI18nKey('WEIRD')).toBe('');
    expect(instanceStatusI18nKey('succeeded')).toBe('run.statusSucceeded');
    expect(instanceStatusI18nKey('weird')).toBe('');
    expect(isFailingNodeState('FAILED')).toBe(true);
    expect(isFailingNodeState('BLOCKED')).toBe(true);
    expect(isFailingNodeState('SUCCEEDED')).toBe(false);
  });
});

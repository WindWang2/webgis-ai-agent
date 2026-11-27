/**
 * Project Exchange API 客户端测试（05 线）——纯逻辑面：
 * blockingConflicts 映射、formatBytes、pollJobTerminal 终态语义。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  blockingConflicts,
  formatBytes,
  pollJobTerminal,
} from './project-exchange';

describe('blockingConflicts', () => {
  it('maps declared_missing and duplicate ids to ack tokens', () => {
    const tokens = blockingConflicts({
      declared_missing: ['standard/layers.gpkg'],
      extra_entries: ['rogue.txt'],
      duplicate_logical_ids: ['ds-schools'],
    });
    expect(tokens).toContain('declared_missing:standard/layers.gpkg');
    expect(tokens).toContain('duplicate:ds-schools');
    // extra_entries 是披露不是阻断（导入可继续）
    expect(tokens.some((tk) => tk.startsWith('extra_entries'))).toBe(false);
  });

  it('returns empty for a clean package', () => {
    expect(blockingConflicts({
      declared_missing: [], extra_entries: [], duplicate_logical_ids: [],
    })).toEqual([]);
  });
});

describe('formatBytes', () => {
  it('formats human readable sizes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});

describe('pollJobTerminal', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns on terminal status without extra polls', async () => {
    const getJobFn = vi.fn()
      .mockResolvedValueOnce({ id: '1', kind: 'analysis', name: 'x', status: 'running' })
      .mockResolvedValueOnce({ id: '1', kind: 'analysis', name: 'x', status: 'completed' });
    const promise = pollJobTerminal('1', 1, 200, getJobFn);
    await vi.advanceTimersByTimeAsync(5);
    const job = await promise;
    expect(job.status).toBe('completed');
    expect(getJobFn).toHaveBeenCalledTimes(2);
  });

  it('throws after exhausting polls on non-terminal status', async () => {
    const getJobFn = vi.fn()
      .mockResolvedValue({ id: '1', kind: 'analysis', name: 'x', status: 'running' });
    const promise = pollJobTerminal('1', 1, 3, getJobFn);
    const assertion = expect(promise).rejects.toThrow('poll timeout');
    await vi.advanceTimersByTimeAsync(20);
    await assertion;
    expect(getJobFn).toHaveBeenCalledTimes(3);
  });
});

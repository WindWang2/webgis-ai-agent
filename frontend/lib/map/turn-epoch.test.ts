/**
 * Turn epoch 单测：epoch 递增 / 订阅退订 / 通知容错。
 *
 * 容错一条对应 review 发现：bumpTurnEpoch 曾同步裸调 listener()，
 * 任一订阅者抛错即中断通知循环并把异常上抛给 cancel/send 调用方
 * （useMapBridge 的取消/发送会因此丢掉后续步骤）。与 receipt-bus 的
 * emit() 保持同样的逐监听器保护。
 */
import { describe, expect, it, vi } from 'vitest';
import { bumpTurnEpoch, currentTurnEpoch, subscribeTurnEpoch } from './turn-epoch';

describe('turn-epoch', () => {
  it('increments the epoch on each bump', () => {
    const before = currentTurnEpoch();
    expect(bumpTurnEpoch()).toBe(before + 1);
    expect(bumpTurnEpoch()).toBe(before + 2);
    expect(currentTurnEpoch()).toBe(before + 2);
  });

  it('notifies subscribers and stops after unsubscribe', () => {
    const first = vi.fn();
    const second = vi.fn();
    const offFirst = subscribeTurnEpoch(first);
    subscribeTurnEpoch(second);

    bumpTurnEpoch();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    offFirst();
    bumpTurnEpoch();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(2);
  });

  it('keeps notifying remaining subscribers when one throws', () => {
    const boom = vi.fn(() => {
      throw new Error('listener exploded');
    });
    const survivor = vi.fn();
    const before = currentTurnEpoch();
    subscribeTurnEpoch(boom);
    subscribeTurnEpoch(survivor);

    let returned: number | undefined;
    expect(() => {
      returned = bumpTurnEpoch();
    }).not.toThrow();
    expect(returned).toBe(before + 1);
    expect(boom).toHaveBeenCalledTimes(1);
    expect(survivor).toHaveBeenCalledTimes(1);
  });
});

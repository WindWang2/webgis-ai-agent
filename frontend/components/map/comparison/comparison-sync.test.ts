import { describe, it, expect, vi } from 'vitest';
import {
  resolveSyncPair,
  clampSwipePosition,
  readCamera,
  applyCameraPatch,
  SWIPE_KEYBOARD_STEP,
} from './comparison-sync';

/**
 * Wave 8 对比工作区 · 同步纯函数测试。
 * 重点：同步无反馈环 —— 同一 source→target 差异应用两次必须收敛为 null。
 */

const A = { center: [116.4, 39.9] as [number, number], zoom: 10, bearing: 0, pitch: 0 };
const B = { center: [121.47, 31.23] as [number, number], zoom: 12, bearing: 30, pitch: 45 };

describe('resolveSyncPair', () => {
  it('双维度全关 → 永不同步（null）', () => {
    expect(resolveSyncPair(A, B, false, false)).toBeNull();
  });

  it('syncPan 只搬 center/bearing/pitch，不搬 zoom', () => {
    const patch = resolveSyncPair(B, A, true, false);
    expect(patch).toEqual({ center: [121.47, 31.23], bearing: 30, pitch: 45 });
  });

  it('syncZoom 只搬 zoom', () => {
    const patch = resolveSyncPair(B, A, false, true);
    expect(patch).toEqual({ zoom: 12 });
  });

  it('全开 → 全量补丁', () => {
    expect(resolveSyncPair(B, A, true, true)).toEqual(B);
  });

  it('幂等收敛：应用补丁后再次求解返回 null（无反馈环的纯函数证明）', () => {
    let target = { ...A };
    const first = resolveSyncPair(B, target, true, true);
    expect(first).not.toBeNull();
    // 模拟 jumpTo 应用补丁
    target = { ...target, ...first };
    // 二次求解：已收敛 → null（运行时不会再次写相机，环在数学上断裂）
    expect(resolveSyncPair(B, target, true, true)).toBeNull();
  });

  it('容差内的浮点噪声视为已收敛', () => {
    const noisy = { ...A, zoom: A.zoom + 1e-9 };
    expect(resolveSyncPair(A, noisy, true, true)).toBeNull();
  });

  it('局部差异只产生最小补丁（仅 zoom 漂移时）', () => {
    const drifted = { ...A, zoom: 11 };
    expect(resolveSyncPair(A, drifted, true, true)).toEqual({ zoom: 10 });
  });
});

describe('clampSwipePosition', () => {
  it('契约区间 0..1 内原样通过', () => {
    expect(clampSwipePosition(0.5)).toBe(0.5);
    expect(clampSwipePosition(0)).toBe(0);
    expect(clampSwipePosition(1)).toBe(1);
  });

  it('越界夹取', () => {
    expect(clampSwipePosition(-0.3)).toBe(0);
    expect(clampSwipePosition(1.4)).toBe(1);
  });

  it('非有限输入收敛为 0（NaN 绝不写入 store）', () => {
    expect(clampSwipePosition(Number.NaN)).toBe(0);
    expect(clampSwipePosition(Number.POSITIVE_INFINITY)).toBe(1);
  });
});

describe('readCamera / applyCameraPatch（IO 适配层）', () => {
  it('readCamera 从 MapLibre 形状的实例读出快照', () => {
    const map = {
      getCenter: () => ({ lng: 116.4, lat: 39.9 }),
      getZoom: () => 10,
      getBearing: () => 20,
      getPitch: () => 60,
    };
    expect(readCamera(map)).toEqual({
      center: [116.4, 39.9],
      zoom: 10,
      bearing: 20,
      pitch: 60,
    });
  });

  it('applyCameraPatch 空补丁不触发 jumpTo', () => {
    const jumpTo = vi.fn();
    applyCameraPatch({ jumpTo }, {});
    expect(jumpTo).not.toHaveBeenCalled();
  });

  it('applyCameraPatch 有补丁时走 jumpTo', () => {
    const jumpTo = vi.fn();
    applyCameraPatch({ jumpTo }, { zoom: 12 });
    expect(jumpTo).toHaveBeenCalledWith({ zoom: 12 });
  });

  it('键盘步进常量契约（±0.02）', () => {
    expect(SWIPE_KEYBOARD_STEP).toBe(0.02);
  });
});

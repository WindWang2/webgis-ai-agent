/**
 * ADR-0283 D3 · 缺瓦片有界账目 —— adapter 账目语义 + runtime handler 接线。
 *
 * - 计数累加、样本封顶（首 5）、源数封顶（8）、快照按 sourceId 字典序
 *   （确定性，无墙钟入序）；无坐标失败只计数；
 * - tileMissDetail 有界（≤200 字符，导出披露消费）；
 * - runtime handler：raster-tiles-{i} 错误带 tile.id → 记账；不带 → 只计数；
 *   非底图源不记账。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import type { Map as MaplibreMap } from 'maplibre-gl';
import {
  _resetTileMissForTests,
  recordTileMiss,
  tileMissDetail,
  tileMissSnapshot,
} from './adapter';
import { MapSpecRuntime } from './runtime';

describe('tile miss account（ADR-0283 D3）', () => {
  beforeEach(() => {
    _resetTileMissForTests();
  });

  it('计数累加 + 样本封顶首 5 个', () => {
    for (let z = 0; z < 8; z++) recordTileMiss('raster-tiles-0', `3/${z}/1`);
    const snap = tileMissSnapshot();
    expect(snap.length).toBe(1);
    expect(snap[0].count).toBe(8);
    expect(snap[0].samples.length).toBe(5);
    expect(snap[0].samples[0]).toBe('3/0/1');
    expect(snap[0].samples[4]).toBe('3/4/1');
  });

  it('无坐标失败只计数不进样本', () => {
    recordTileMiss('raster-tiles-1', '');
    recordTileMiss('raster-tiles-1', '4/2/2');
    const snap = tileMissSnapshot();
    expect(snap[0].count).toBe(2);
    expect(snap[0].samples).toEqual(['4/2/2']);
  });

  it('源数封顶 8（防御性；超出丢弃不挤占既有源）', () => {
    for (let i = 0; i < 12; i++) recordTileMiss(`raster-tiles-${i}`, '1/0/0');
    expect(tileMissSnapshot().length).toBe(8);
  });

  it('快照按 sourceId 字典序（确定性）', () => {
    recordTileMiss('raster-tiles-2', '1/0/0');
    recordTileMiss('raster-tiles-0', '1/0/1');
    recordTileMiss('raster-tiles-1', '1/0/2');
    expect(tileMissSnapshot().map((a) => a.sourceId)).toEqual([
      'raster-tiles-0',
      'raster-tiles-1',
      'raster-tiles-2',
    ]);
  });

  it('tileMissDetail 有界 ≤200 且含计数与样本', () => {
    for (let i = 0; i < 40; i++) recordTileMiss('raster-tiles-0', `12/${i}/34`);
    const detail = tileMissDetail(tileMissSnapshot()[0]);
    expect(detail.length).toBeLessThanOrEqual(200);
    expect(detail).toContain('src=raster-tiles-0');
    expect(detail).toContain('count=40');
    expect(detail).toContain('sample=12/0/34');
  });
});

function errorCapableMockMap() {
  const handlers = new Map<string, Set<(e?: unknown) => void>>();
  const map = {
    on(event: string, handler: (e?: unknown) => void) {
      const set = handlers.get(event) ?? new Set();
      set.add(handler);
      handlers.set(event, set);
    },
    off(event: string, handler: (e?: unknown) => void) {
      handlers.get(event)?.delete(handler);
    },
    isStyleLoaded: () => true,
    getStyle: () => ({ sources: {}, layers: [] }),
    getSource: () => undefined,
    getLayer: () => undefined,
    _emitError(e: unknown) {
      for (const handler of Array.from(handlers.get('error') ?? [])) handler(e);
    },
  };
  return map;
}

describe('runtime handler 记账接线（ADR-0283 D3）', () => {
  beforeEach(() => {
    _resetTileMissForTests();
  });

  it('raster-tiles 错误带 tile.id → 计数+样本', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map as unknown as MaplibreMap);
    map._emitError({ sourceId: 'raster-tiles-0', tile: { id: '6/33/21' } });
    map._emitError({ sourceId: 'raster-tiles-0', tile: { id: '6/33/22' } });
    const snap = tileMissSnapshot();
    expect(snap.length).toBe(1);
    expect(snap[0].count).toBe(2);
    expect(snap[0].samples).toEqual(['6/33/21', '6/33/22']);
    void rt;
  });

  it('tile.id 缺席 → 只计数（不虚构坐标）', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map as unknown as MaplibreMap);
    map._emitError({ sourceId: 'raster-tiles-0' });
    const snap = tileMissSnapshot();
    expect(snap[0].count).toBe(1);
    expect(snap[0].samples).toEqual([]);
    void rt;
  });

  it('非底图源不记账', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map as unknown as MaplibreMap);
    map._emitError({ sourceId: 'user-layer-1', tile: { id: '6/1/1' } });
    expect(tileMissSnapshot()).toEqual([]);
    void rt;
  });
});

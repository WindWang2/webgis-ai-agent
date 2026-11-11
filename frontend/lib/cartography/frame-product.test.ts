/**
 * G15 前端契约测试：frame-sync 同步组纯函数 + frame-product 投影/导出
 * 路由决策 + spec-frames 产品适配器的诚实披露。
 */
import { describe, expect, it } from 'vitest';

import {
  resolveGroupSync,
  syncGroupMembers,
  type CameraSnapshot,
} from '@/lib/cartography/frame-sync';
import {
  frameProductExportDecision,
  projectFrameProduct,
} from '@/lib/cartography/frame-product';
import {
  productFramesToExportFrames,
  specFramesToExportFrames,
} from '@/lib/map-kit/spec-frames';

const cam = (lng: number, zoom: number): CameraSnapshot => ({
  center: [lng, 30.0], zoom, bearing: 0, pitch: 0,
});

describe('resolveGroupSync（N 方同步组）', () => {
  const target = (id: string, group: string | null, camera: CameraSnapshot) => ({
    id, syncGroup: group, camera, map: { jumpTo: () => {} },
  });

  it('同组传播，组外不传播', () => {
    const src = cam(104.0, 10);
    const inA = target('f2', 'A', cam(103.0, 10));
    const inB = target('f3', 'B', cam(103.0, 10));
    const free = target('f4', null, cam(103.0, 10));
    const out = resolveGroupSync('f1', src,
      [target('f1', 'A', cam(104.0, 10)), inA, inB, free], true, true);
    expect(out.patches.map((p) => p.id)).toEqual(['f2']);
    // f2 的补丁真的收敛到源相机（无回声 —— 幂等断点）。
    const again = resolveGroupSync('f1', src,
      [target('f1', 'A', src), target('f2', 'A', src), inB, free], true, true);
    expect(again.patches).toHaveLength(0);
  });

  it('zoom 关闭时 zoom 不传播', () => {
    const src = cam(104.0, 12);
    const out = resolveGroupSync('f1', src,
      [target('f1', 'A', cam(104.0, 10)), target('f2', 'A', cam(104.0, 10))],
      true, false);
    expect(out.patches).toHaveLength(0); // 只有 zoom 差异，pan 已一致
  });

  it('无组源不传播', () => {
    const out = resolveGroupSync('f1', cam(104, 10),
      [target('f1', null, cam(104, 10)), target('f2', 'A', cam(103, 10))],
      true, true);
    expect(out.patches).toHaveLength(0);
  });

  it('组成员表保序', () => {
    const members = syncGroupMembers([
      { frameId: 'a', syncGroup: 'A' },
      { frameId: 'b', syncGroup: 'B' },
      { frameId: 'c', syncGroup: 'A' },
      { frameId: 'd', syncGroup: null },
    ]);
    expect(members.get('A')).toEqual(['a', 'c']);
    expect(members.get('B')).toEqual(['b']);
    expect(members.has('d' as string)).toBe(false);
  });
});

describe('projectFrameProduct（MapSpec 投影）', () => {
  const spec = {
    version: '2.0',
    layout: {
      frames: [
        { id: 'f1', title: '2022', extent: [1, 2, 3, 4] },
        { id: 'f2', title: '2024', extent: [1, 2, 3, 4], enabled: false },
      ],
    },
    frame_product: {
      schema_version: 'frame_product.v1',
      enabled: true,
      product_kind: 'time_series',
      grid: { columns: 2, title: '两年' },
      frames: [
        { frame_id: 'f1', legend: 'shared',
          data: { ref: 'r', layer_id: 'l', field: 'v', content_revision: 2,
                  where: { op: 'eq', field: 'year', value: 2022 } } },
        { frame_id: 'f2', legend: 'independent' },
      ],
      shared_expression: {
        field: 'v', domain_status: 'complete', breaks: [0, 5, 10],
        k: 2, palette: 'Blues',
      },
      active_frame: 'f2',
    },
  } as never;

  it('joins product slots with layout frame identity', () => {
    const view = projectFrameProduct(spec);
    expect(view.bound).toBe(true);
    expect(view.slots).toHaveLength(2);
    expect(view.slots[0]).toMatchObject({
      frameId: 'f1', title: '2022', enabled: true, legend: 'shared',
      active: false,
    });
    expect(view.slots[1]).toMatchObject({ frameId: 'f2', enabled: false, active: true });
    expect(view.slots[0].binding).toMatchObject({
      ref: 'r', field: 'v', contentRevision: 2, hasWhere: true,
    });
    expect(view.sharedExpression).toMatchObject({
      field: 'v', domainStatus: 'complete', breaksCount: 3,
    });
  });

  it('absent product → unbound view', () => {
    const view = projectFrameProduct({ version: '2.0' } as never);
    expect(view.bound).toBe(false);
    expect(view.slots).toHaveLength(0);
  });

  it('export decision: pdf routes to backend publication', () => {
    expect(frameProductExportDecision(spec, 'pdf')).toEqual({
      backendPublication: true, limitations: [],
    });
  });

  it('export decision: png stays client with honest limitations', () => {
    const d = frameProductExportDecision(spec, 'png');
    expect(d.backendPublication).toBe(false);
    expect(d.limitations).toContain('client_canvas_no_layer_overrides');
    expect(frameProductExportDecision({ version: '2.0' } as never, 'png')
      .backendPublication).toBe(false);
  });
});

describe('productFramesToExportFrames（诚实适配器）', () => {
  const spec = {
    layout: {
      frames: [
        { id: 'f1', title: '2022', extent: [1, 2, 3, 4] },
        { id: 'f2', title: '2024', enabled: false, extent: [1, 2, 3, 4] },
      ],
    },
    frame_product: {
      enabled: true,
      frames: [
        { frame_id: 'f1', title: '2022',
          data: { layer_id: 'l', where: { op: 'eq', field: 'year', value: 2022 } } },
        { frame_id: 'f2' },
      ],
    },
  };

  it('projects where filters for canvas export + discloses limitations', () => {
    const out = productFramesToExportFrames(spec as never);
    expect(out.productPresent).toBe(true);
    expect(out.frames).toHaveLength(1);
    expect(out.frames[0]).toMatchObject({
      title: '2022',
      where: { layerId: 'l', field: 'year', equal: 2022 },
    });
    expect(out.disabledCount).toBe(1);
    expect(out.limitations.length).toBeGreaterThan(0);
  });

  it('no product → not present, legacy adapter untouched', () => {
    const out = productFramesToExportFrames({ layout: { frames: [] } } as never);
    expect(out.productPresent).toBe(false);
    // 旧 adapter 语义不变。
    const legacy = specFramesToExportFrames([
      { id: 'a', title: 'A', extent: [1, 2, 3, 4] },
    ]);
    expect(legacy.frames).toHaveLength(1);
  });
});

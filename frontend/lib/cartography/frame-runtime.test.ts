/**
 * G25 运行时契约测试：状态机（unmounted/loading/live/snapshot/stale/error）、
 * 快照≠live、旧 revision 不得 verified、确定性后继、extent 锁守卫、投影拒绝。
 */
import { describe, expect, it } from 'vitest';

import type { CameraSnapshot } from '@/lib/cartography/frame-sync';
import {
  cameraWriteGuard,
  checkProjectionCompat,
  projectFrameRuntime,
  selectActiveSuccessor,
  snapshotFingerprint,
  type FrameRuntimeInputs,
  type MountedFrameFact,
  type SnapshotFact,
} from '@/lib/cartography/frame-runtime';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

const cam = (lng: number, zoom: number): CameraSnapshot => ({
  center: [lng, 30], zoom, bearing: 0, pitch: 0,
});

/** 三画幅产品 spec（G15 J16 夹具同构：2020/2022/2024，组 A/A/B）。 */
function threeFrameSpec(): MapSpec {
  return {
    view: { center: [104.0, 30.6], zoom: 9 },
    sources: {},
    layers: [],
    frame_product: {
      enabled: true,
      product_kind: 'time_series',
      grid: { columns: 3, title: '成都学位' },
      active_frame: 'fp-t-2020',
      frames: [
        {
          frame_id: 'fp-t-2020',
          title: '2020',
          sync_group: 'A',
          data: {
            ref: 'ref:chengdu:1',
            layer_id: 'layer-0',
            field: 'per_10k',
            content_revision: 7,
            where: { op: 'eq', field: 'year', value: 2020 },
          },
        },
        {
          frame_id: 'fp-t-2022',
          title: '2022',
          sync_group: 'A',
          data: {
            ref: 'ref:chengdu:1',
            layer_id: 'layer-0',
            field: 'per_10k',
            content_revision: 7,
            where: { op: 'eq', field: 'year', value: 2022 },
          },
        },
        {
          frame_id: 'fp-t-2024',
          title: '2024',
          sync_group: 'B',
          data: {
            ref: 'ref:chengdu:1',
            layer_id: 'layer-0',
            field: 'per_10k',
            content_revision: 9,
            where: { op: 'eq', field: 'year', value: 2024 },
          },
        },
      ],
    },
    layout: {
      frames: [
        { id: 'fp-t-2020', title: '2020', enabled: true },
        { id: 'fp-t-2022', title: '2022', enabled: true },
        { id: 'fp-t-2024', title: '2024', enabled: true },
      ],
    },
  } as unknown as MapSpec;
}

function inputs(over: Partial<FrameRuntimeInputs> = {}): FrameRuntimeInputs {
  return {
    spec: threeFrameSpec(),
    specRevision: 12,
    activeFrameId: 'fp-t-2020',
    mounted: new Map<string, MountedFrameFact>(),
    snapshots: new Map<string, SnapshotFact>(),
    cameraDrafts: new Map(),
    expectedCameras: new Map(),
    viewportSize: { width: 400, height: 300 },
    ...over,
  };
}

describe('projectFrameRuntime 状态分类', () => {
  it('活跃帧：无实例 loading，版本匹配 live，落后 stale，错误 error', () => {
    const loading = projectFrameRuntime(inputs());
    expect(loading.find((s) => s.frameId === 'fp-t-2020')?.mountState).toBe('loading');

    const live = projectFrameRuntime(inputs({
      mounted: new Map([
        ['fp-t-2020', {
          frameId: 'fp-t-2020', generation: 1,
          resolvedSpecRevision: 12, resolvedDataRevision: 7, errorCode: null,
        }],
      ]),
    }));
    expect(live.find((s) => s.frameId === 'fp-t-2020')?.mountState).toBe('live');

    const stale = projectFrameRuntime(inputs({
      mounted: new Map([
        ['fp-t-2020', {
          frameId: 'fp-t-2020', generation: 1,
          resolvedSpecRevision: 11, resolvedDataRevision: 7, errorCode: null,
        }],
      ]),
    }));
    expect(stale.find((s) => s.frameId === 'fp-t-2020')?.mountState).toBe('stale');

    const err = projectFrameRuntime(inputs({
      mounted: new Map([
        ['fp-t-2020', {
          frameId: 'fp-t-2020', generation: 1,
          resolvedSpecRevision: 12, resolvedDataRevision: 7,
          errorCode: 'source_error', errorMessage: 'ref unresolvable',
        }],
      ]),
    }));
    const slot = err.find((s) => s.frameId === 'fp-t-2020');
    expect(slot?.mountState).toBe('error');
    expect(slot?.error?.code).toBe('source_error');
  });

  it('非活跃帧：指纹匹配 snapshot；spec/数据刷新后旧快照 stale（A11/A12）', () => {
    const snap: SnapshotFact = {
      frameId: 'fp-t-2022', fingerprint: 'aaaa1111',
      specRevision: 12, dataRevision: 7,
      viewportSize: { width: 400, height: 300 },
    };
    const fresh = projectFrameRuntime(inputs({
      snapshots: new Map([['fp-t-2022', snap]]),
    }));
    const s2 = fresh.find((s) => s.frameId === 'fp-t-2022')!;
    expect(s2.mountState).toBe('snapshot');
    expect(s2.snapshotCurrent).toBe(true);

    // spec revision 前进 → 同一快照变 stale，绝不宣称 live/verified。
    const afterSpecBump = projectFrameRuntime(inputs({
      specRevision: 13,
      snapshots: new Map([['fp-t-2022', snap]]),
    }));
    expect(afterSpecBump.find((s) => s.frameId === 'fp-t-2022')?.mountState).toBe('stale');

    // 数据 revision 前进（帧绑定 content_revision 变化）→ 同样 stale。
    const specDataBump = threeFrameSpec();
    (specDataBump.frame_product!.frames[1].data as { content_revision: number }).content_revision = 8;
    const afterDataBump = projectFrameRuntime(inputs({
      spec: specDataBump,
      snapshots: new Map([['fp-t-2022', snap]]),
    }));
    expect(afterDataBump.find((s) => s.frameId === 'fp-t-2022')?.mountState).toBe('stale');
  });

  it('viewport 尺寸变化 → 旧尺寸快照不匹配（指纹含 w×h）', () => {
    const snap: SnapshotFact = {
      frameId: 'fp-t-2022', fingerprint: 'aaaa1111',
      specRevision: 12, dataRevision: 7,
      viewportSize: { width: 400, height: 300 },
    };
    const resized = projectFrameRuntime(inputs({
      snapshots: new Map([['fp-t-2022', snap]]),
      viewportSize: { width: 800, height: 600 },
    }));
    expect(resized.find((s) => s.frameId === 'fp-t-2022')?.mountState).toBe('stale');
  });

  it('frame 从 spec 消失 → 残留 mounted/snapshot 不复活身份（A13）', () => {
    const spec = threeFrameSpec();
    spec.frame_product!.frames = spec.frame_product!.frames.filter(
      (fr) => fr.frame_id !== 'fp-t-2022',
    );
    spec.layout!.frames = spec.layout!.frames.filter((lf) => lf.id !== 'fp-t-2022');
    const out = projectFrameRuntime(inputs({
      spec,
      activeFrameId: 'fp-t-2020',
      mounted: new Map([
        ['fp-t-2022', {
          frameId: 'fp-t-2022', generation: 1,
          resolvedSpecRevision: 12, resolvedDataRevision: 7, errorCode: null,
        }],
      ]),
      snapshots: new Map([['fp-t-2022', {
        frameId: 'fp-t-2022', fingerprint: 'x', specRevision: 12,
        dataRevision: 7, viewportSize: { width: 400, height: 300 },
      }]]),
    }));
    expect(out.map((s) => s.frameId)).toEqual(['fp-t-2020', 'fp-t-2024']);
  });

  it('未绑定产品 → 空投影；camera draft/expected 分别来自两个输入面', () => {
    expect(projectFrameRuntime(inputs({ spec: null }))).toEqual([]);
    const out = projectFrameRuntime(inputs({
      cameraDrafts: new Map([['fp-t-2022', cam(104.1, 9.5)]]),
      expectedCameras: new Map([['fp-t-2024', cam(104.2, 10)]]),
    }));
    expect(out.find((s) => s.frameId === 'fp-t-2022')?.cameraDraft?.zoom).toBe(9.5);
    expect(out.find((s) => s.frameId === 'fp-t-2024')?.expectedCamera?.center[0]).toBe(104.2);
  });
});

describe('snapshotFingerprint', () => {
  it('同输入同指纹；任一维度变化指纹变化', () => {
    const base = {
      sessionId: 's-1', frameId: 'fp-1', specRevision: 3,
      dataRevision: 5, viewportSize: { width: 400, height: 300 },
    };
    const fp = snapshotFingerprint(base);
    expect(snapshotFingerprint(base)).toBe(fp);
    expect(snapshotFingerprint({ ...base, specRevision: 4 })).not.toBe(fp);
    expect(snapshotFingerprint({ ...base, dataRevision: null })).not.toBe(fp);
    expect(snapshotFingerprint({ ...base, viewportSize: { width: 401, height: 300 } })).not.toBe(fp);
    expect(snapshotFingerprint({ ...base, frameId: 'fp-2' })).not.toBe(fp);
  });
});

describe('selectActiveSuccessor（确定性后继）', () => {
  it('删中者 → 下一个可用帧；环形回绕；无可用 → null', () => {
    expect(selectActiveSuccessor(['a', 'b', 'c'], 'b')).toBe('c');
    expect(selectActiveSuccessor(['a', 'b', 'c'], 'c')).toBe('a');
    expect(selectActiveSuccessor(['a', 'b', 'c'], null)).toBe('a');
    expect(selectActiveSuccessor(['a', 'b', 'c'], 'b', (id) => id !== 'c')).toBe('a');
    expect(selectActiveSuccessor(['a'], 'a')).toBeNull();
    expect(selectActiveSuccessor([], null)).toBeNull();
  });
});

describe('守卫（extent 锁 / 投影兼容）', () => {
  it('锁定 extent 拒绝相机写入；未锁放行', () => {
    expect(cameraWriteGuard({ id: 'f1', extent: [103, 30, 105, 31] }).ok).toBe(false);
    expect(cameraWriteGuard({ id: 'f1' }).ok).toBe(true);
    expect(cameraWriteGuard(null).ok).toBe(true);
  });
  it('投影不一致明确拒绝；未声明视为 3857 兼容（A08）', () => {
    expect(checkProjectionCompat('EPSG:3857', 'EPSG:3857').ok).toBe(true);
    expect(checkProjectionCompat(null, null).ok).toBe(true);
    expect(checkProjectionCompat('EPSG:4326', null).ok).toBe(false);
    const bad = checkProjectionCompat('EPSG:4326', 'EPSG:3857');
    expect(bad.ok).toBe(false);
    expect(bad.reason).toContain('projection_mismatch');
  });
});

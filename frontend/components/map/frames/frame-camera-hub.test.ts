/**
 * G25 相机事件总线测试 —— 真实事件流上的防回声、同组传播、异组隔离、
 * 主图联动、锁定 extent 拒绝、未挂载帧期望相机、代际失效。
 */
import { describe, expect, it, vi } from 'vitest';

import { FrameCameraHub, type HubMapLike } from '@/components/map/frames/frame-camera-hub';

/** 可脚本驱动的假 map（记录 jumpTo 调用；move 事件手动派发）。 */
function fakeMap(cam: { lng: number; lat: number; zoom: number; bearing?: number; pitch?: number }) {
  const state = { ...cam, bearing: cam.bearing ?? 0, pitch: cam.pitch ?? 0 };
  const jumps: Array<Record<string, unknown>> = [];
  const listeners = new Set<() => void>();
  const map: HubMapLike = {
    on: (_e, h) => listeners.add(h),
    off: (_e, h) => listeners.delete(h),
    getCenter: () => ({ lng: state.lng, lat: state.lat }),
    getZoom: () => state.zoom,
    getBearing: () => state.bearing,
    getPitch: () => state.pitch,
    jumpTo: (opts) => {
      jumps.push(opts);
      if (typeof opts.center === 'object') {
        state.lng = opts.center[0];
        state.lat = opts.center[1];
      }
      if (typeof opts.zoom === 'number') state.zoom = opts.zoom;
      if (typeof opts.bearing === 'number') state.bearing = opts.bearing;
      if (typeof opts.pitch === 'number') state.pitch = opts.pitch;
      for (const l of listeners) l(); // maplibre: jumpTo 同步派发 move
    },
  };
  return {
    map,
    jumps,
    fire: () => {
      for (const l of listeners) l();
    },
    state,
  };
}

describe('FrameCameraHub', () => {
  it('同组 pan/zoom 传播；异组/无组不传播（A04/A05/A06）', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: false });
    const f1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const f2 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const f3 = fakeMap({ lng: 104, lat: 30, zoom: 9 }); // 组 B
    const f4 = fakeMap({ lng: 104, lat: 30, zoom: 9 }); // 无组
    hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.attach({ id: 'f2', kind: 'frame', map: f2.map, syncGroup: 'A', generation: 1 });
    hub.attach({ id: 'f3', kind: 'frame', map: f3.map, syncGroup: 'B', generation: 1 });
    hub.attach({ id: 'f4', kind: 'frame', map: f4.map, syncGroup: null, generation: 1 });
    hub.setActiveFrame('f1');

    f1.state.lng = 105.5;
    f1.state.zoom = 11;
    f1.fire();
    expect(f2.state.lng).toBeCloseTo(105.5);
    expect(f2.state.zoom).toBeCloseTo(11);
    expect(f3.jumps).toHaveLength(0); // 异组
    expect(f4.jumps).toHaveLength(0); // 无组
  });

  it('无回声：源事件触发的写入不再反向回写源（A07）', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: false });
    const f1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const f2 = fakeMap({ lng: 103, lat: 30, zoom: 8 });
    hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.attach({ id: 'f2', kind: 'frame', map: f2.map, syncGroup: 'A', generation: 1 });
    hub.setActiveFrame('f1');

    f1.state.lng = 106;
    f1.fire(); // f1.move → hub 写 f2（jumpTo 同步再发 f2.move → 被吞）
    expect(f2.state.lng).toBeCloseTo(106);
    // f2 的 jumpTo 恰好一次；若回声未被吞，f2.move 会再写 f1 → f1 跳走。
    expect(f2.jumps).toHaveLength(1);
    expect(f1.state.lng).toBeCloseTo(106);
    // 收敛后重复事件（同相机再 fire）不产生新写入。
    f1.fire();
    expect(f2.jumps).toHaveLength(1);
  });

  it('主图 ↔ 活动画幅双向联动；非活动帧不联动主图', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: true });
    const main = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const f1 = fakeMap({ lng: 103, lat: 30, zoom: 9 });
    const f2 = fakeMap({ lng: 103, lat: 30, zoom: 9 });
    hub.attach({ id: 'main', kind: 'main', map: main.map, syncGroup: null, generation: 1 });
    hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.attach({ id: 'f2', kind: 'frame', map: f2.map, syncGroup: null, generation: 1 });
    hub.setActiveFrame('f1');

    main.state.lng = 107;
    main.fire();
    expect(f1.state.lng).toBeCloseTo(107); // 主图 → 活动画幅
    expect(f2.jumps).toHaveLength(0); // 非活动帧不受主图联动

    f1.state.zoom = 12;
    f1.fire();
    expect(main.state.zoom).toBeCloseTo(12); // 活动画幅 → 主图
  });

  it('锁定 extent 帧拒绝相机写入（A17）', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: false });
    const f1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const locked = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.attach({ id: 'fl', kind: 'frame', map: locked.map, syncGroup: 'A', generation: 1, lockedExtent: true });
    hub.setActiveFrame('f1');

    f1.state.lng = 108;
    f1.fire();
    expect(locked.jumps).toHaveLength(0);
    expect(locked.state.lng).toBeCloseTo(104);
  });

  it('未挂载同组帧记录期望相机；异组不记录（P3 契约）', () => {
    const onExpected = vi.fn();
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: false, onExpectedCamera: onExpected });
    const f1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.setActiveFrame('f1');
    hub.noteUnmounted([
      { frameId: 'f2', syncGroup: 'A' },
      { frameId: 'f3', syncGroup: 'B' },
    ]);

    f1.state.lng = 109.25;
    f1.state.zoom = 10.5;
    f1.fire();
    expect(onExpected).toHaveBeenCalledTimes(1);
    expect(onExpected).toHaveBeenCalledWith('f2', expect.objectContaining({ center: [109.25, 30], zoom: 10.5 }), 'f1');
  });

  it('旧代际事件被丢弃；重复 attach 先解绑旧实例（A13/A16）', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: false });
    const gen1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const gen2 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const other = fakeMap({ lng: 103, lat: 30, zoom: 9 });
    hub.attach({ id: 'other', kind: 'frame', map: other.map, syncGroup: 'A', generation: 1 });
    const detach1 = hub.attach({ id: 'f1', kind: 'frame', map: gen1.map, syncGroup: 'A', generation: 1 });
    hub.setActiveFrame('f1');

    // context loss → 重建：detach 旧 + attach 新代际。
    detach1();
    hub.attach({ id: 'f1', kind: 'frame', map: gen2.map, syncGroup: 'A', generation: 2 });

    gen1.state.lng = 120; // 旧实例迟到事件（仍会触发其自身 handler）
    gen1.fire();
    expect(other.jumps).toHaveLength(0); // 旧代际事件不传播

    gen2.state.lng = 105;
    gen2.fire();
    expect(other.state.lng).toBeCloseTo(105); // 新代际正常传播
    expect(hub.memberIds()).toEqual(['other', 'f1']);
  });

  it('zoom 维度关闭时 zoom 不传播（协议独立可控）', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: false, linkMain: false });
    const f1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const f2 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.attach({ id: 'f2', kind: 'frame', map: f2.map, syncGroup: 'A', generation: 1 });
    hub.setActiveFrame('f1');

    f1.state.zoom = 13;
    f1.fire();
    expect(f2.jumps).toHaveLength(0); // 只有 zoom 差异，pan 一致 → 无写入

    f1.state.lng = 110;
    f1.fire();
    expect(f2.state.lng).toBeCloseTo(110);
    expect(f2.state.zoom).toBeCloseTo(9); // zoom 保持原值
  });

  it('dispose 解绑全部监听', () => {
    const hub = new FrameCameraHub({ syncPan: true, syncZoom: true, linkMain: false });
    const f1 = fakeMap({ lng: 104, lat: 30, zoom: 9 });
    const detach = hub.attach({ id: 'f1', kind: 'frame', map: f1.map, syncGroup: 'A', generation: 1 });
    hub.dispose();
    f1.state.lng = 111;
    f1.fire(); // 不抛错、不传播
    expect(hub.memberIds()).toEqual([]);
    detach(); // 幂等
  });
});

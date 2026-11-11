/**
 * G25 活地图实例登记簿（P1）—— 主图/画幅/对比副图统一计数与预算仲裁。
 *
 * 动机（A09）：浏览器 WebGL context 上限（通常 8–16），G15 PLAN 自述
 * 「活跃 MapLibre ≤2 + 快照占位」未交付。本登记簿是**唯一**活实例计数面：
 * - map-panel 主图挂载时 register('main')；ComparisonView 副图 register
 *   ('comparison')（小接入补丁）；FrameViewport register('frame:{id}')。
 * - FrameGrid 调度依据：liveCount() < budget 才允许挂载活动画幅视窗；
 *   预算被对比视图占用时如实披露，不强行突破。
 * - 预算默认 2（主图 + 活动画幅计入总数，不另算）；仅诊断/测试可调。
 *
 * 代际：每次 register 分配递增 generation —— context loss 重建后新注册
 * 的 generation 更大，旧注册的 release() 不影响计数（A16/A26 的泄漏防线）。
 */

export type MapInstanceKind = 'main' | 'frame' | 'comparison';

export interface MapInstanceRegistration {
  /** 登记键（如 'main'、'frame:fp-t-2022'、'comparison'）。 */
  key: string;
  kind: MapInstanceKind;
  generation: number;
  /** release 后 false（迟到 release 的幂等防线）。 */
  active: boolean;
  registeredAt: number;
}

export interface MapInstanceRegistrationHandle {
  key: string;
  kind: MapInstanceKind;
  generation: number;
  /** 注销（幂等；旧代际迟到注销不误删新注册）。 */
  release: () => void;
}

/** 默认活地图预算：主图 + 活动画幅 = 2（A09 契约值）。 */
export const DEFAULT_LIVE_MAP_BUDGET = 2;

type Listener = () => void;

class MapInstanceRegistry {
  private entries = new Map<string, MapInstanceRegistration>();
  private listeners = new Set<Listener>();
  private generationSeq = 0;
  private budgetValue = DEFAULT_LIVE_MAP_BUDGET;

  /** 挂载登记（同 key 重复注册 = 重建：旧条目立即失效）。 */
  register(kind: MapInstanceKind, key: string): MapInstanceRegistrationHandle {
    const previous = this.entries.get(key);
    if (previous) previous.active = false; // context 重建：旧代际让位
    const generation = ++this.generationSeq;
    const entry: MapInstanceRegistration = {
      key, kind, generation, active: true, registeredAt: Date.now(),
    };
    this.entries.set(key, entry);
    this.emit();
    const registry = this;
    let released = false;
    return {
      key,
      kind,
      generation,
      release() {
        if (released) return;
        released = true;
        const cur = registry.entries.get(key);
        // 只有当前代际才真注销（迟到 release 不影响新注册）。
        if (cur && cur.generation === generation && cur.active) {
          cur.active = false;
          registry.entries.delete(key);
          registry.emit();
        }
      },
    };
  }

  liveCount(): number {
    return this.entries.size;
  }

  /** 各类计数（诊断/观察 refs-only）。 */
  liveCountsByKind(): Record<MapInstanceKind, number> {
    const out = { main: 0, frame: 0, comparison: 0 } as Record<MapInstanceKind, number>;
    for (const e of this.entries.values()) out[e.kind] += 1;
    return out;
  }

  budget(): number {
    return this.budgetValue;
  }

  /** 仅诊断/测试：调整预算（生产默认 2，不因配置膨胀）。 */
  setBudgetForDiagnostics(n: number): void {
    this.budgetValue = Math.max(1, Math.floor(n));
    this.emit();
  }

  has(key: string): boolean {
    return this.entries.has(key);
  }

  /** refs-only 快照（观察/断言面；不含实例引用）。 */
  snapshot(): MapInstanceRegistration[] {
    return [...this.entries.values()].map((e) => ({ ...e }));
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** 测试隔离：全清（生产代码不得调用）。 */
  resetForTest(): void {
    this.entries.clear();
    this.listeners.clear();
    this.budgetValue = DEFAULT_LIVE_MAP_BUDGET;
    this.generationSeq = 0;
  }

  private emit(): void {
    for (const l of this.listeners) l();
  }
}

/** 模块级单例（与 live-frame/selection-store 同模式）。 */
export const mapInstanceRegistry = new MapInstanceRegistry();

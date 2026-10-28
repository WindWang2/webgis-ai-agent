/**
 * scene3d/resource-budget — 跨资产累计资源预算账本（G3DV2-B M3）。
 *
 * 背景（#1834 EVIDENCE 残留 2 / review P1-3）：既有防线是 per-parse 预算
 * （5M 三角 / 64MB 单 accessor / 256MB 单 parse）+ per-asset LRU 96 条 +
 * per-asset 并发 4 —— 乘积上界仍可累计大内存（96 tile × 256MB × N 资产）。
 * 本账本把 geometry/texture/三角/CPU 解码/材质五维累计到一个 manager 级
 * 执行面：超限先逐出不可见 tile（LRU），仍超则新载入 typed degraded
 * （budget_exceeded），可见内容永不因预算被静默丢弃。
 *
 * 纯函数式记账（jsdom 可测）；释放语义 = owner 键（assetId×tileId）粒度，
 * 与 BuiltModel.dispose 生命周期一一对应。
 */

export interface ResourceBudgetAmounts {
  geometryBytes?: number;
  textureBytes?: number;
  triangles?: number;
  cpuDecodedBytes?: number;
  materials?: number;
}

export interface ResourceBudgetLimits {
  maxGeometryBytes: number;
  maxTextureBytes: number;
  maxTriangles: number;
  maxCpuDecodedBytes: number;
  maxMaterials: number;
}

/**
 * 默认累计预算（单 map / 单 manager 域）：
 * - 几何 256MB / 纹理 128MB：SwiftShader 与中低端 GPU 的诚实上限；
 * - 三角 10M：per-asset 5M 上限之上的跨资产收口；
 * - CPU 解码 512MB：per-parse 256MB 之上的 2 倍余量（软渲染峰值可控）；
 * - 材质 4096：Each material ~对象级开销，防线为异常数据集（不是正常上限）。
 */
export const DEFAULT_SCENE_RESOURCE_BUDGET: ResourceBudgetLimits = {
  maxGeometryBytes: 256 * 1024 * 1024,
  maxTextureBytes: 128 * 1024 * 1024,
  maxTriangles: 10_000_000,
  maxCpuDecodedBytes: 512 * 1024 * 1024,
  maxMaterials: 4096,
};

export type ResourceBudgetDimension = keyof ResourceBudgetLimits;

export interface SceneResourceUsage {
  geometryBytes: number;
  textureBytes: number;
  triangles: number;
  cpuDecodedBytes: number;
  materials: number;
}

type Amounts = Required<ResourceBudgetAmounts>;

const EMPTY: Amounts = {
  geometryBytes: 0,
  textureBytes: 0,
  triangles: 0,
  cpuDecodedBytes: 0,
  materials: 0,
};

export class SceneResourceBudgetLedger {
  private readonly limits: ResourceBudgetLimits;
  /** ownerKey（assetId\u0000tileId|single）→ 已记账量。 */
  private readonly owners = new Map<string, Amounts>();

  constructor(limits: Partial<ResourceBudgetLimits> = {}) {
    this.limits = { ...DEFAULT_SCENE_RESOURCE_BUDGET, ...limits };
  }

  limitsView(): Readonly<ResourceBudgetLimits> {
    return this.limits;
  }

  usage(): SceneResourceUsage {
    const out: Amounts = { ...EMPTY };
    for (const a of this.owners.values()) {
      out.geometryBytes += a.geometryBytes;
      out.textureBytes += a.textureBytes;
      out.triangles += a.triangles;
      out.cpuDecodedBytes += a.cpuDecodedBytes;
      out.materials += a.materials;
    }
    return out;
  }

  ownerKeys(): string[] {
    return [...this.owners.keys()];
  }

  /**
   * 若计入 `pending` 会超哪个维度（返回维度名；null = 在预算内）。
   * 只做判定不记账 —— 调用方先 enforceBudget 逐出再决定 acquire 或降级。
   */
  wouldExceed(pending: ResourceBudgetAmounts): ResourceBudgetDimension | null {
    const u = this.usage();
    if (u.geometryBytes + (pending.geometryBytes ?? 0) > this.limits.maxGeometryBytes) return 'maxGeometryBytes';
    if (u.textureBytes + (pending.textureBytes ?? 0) > this.limits.maxTextureBytes) return 'maxTextureBytes';
    if (u.triangles + (pending.triangles ?? 0) > this.limits.maxTriangles) return 'maxTriangles';
    if (u.cpuDecodedBytes + (pending.cpuDecodedBytes ?? 0) > this.limits.maxCpuDecodedBytes) return 'maxCpuDecodedBytes';
    if (u.materials + (pending.materials ?? 0) > this.limits.maxMaterials) return 'maxMaterials';
    return null;
  }

  acquire(ownerKey: string, amounts: ResourceBudgetAmounts): void {
    const existing = this.owners.get(ownerKey) ?? { ...EMPTY };
    existing.geometryBytes += amounts.geometryBytes ?? 0;
    existing.textureBytes += amounts.textureBytes ?? 0;
    existing.triangles += amounts.triangles ?? 0;
    existing.cpuDecodedBytes += amounts.cpuDecodedBytes ?? 0;
    existing.materials += amounts.materials ?? 0;
    this.owners.set(ownerKey, existing);
  }

  /** 释放 owner 的全部记账（模型 dispose 时调用；未知 owner 幂等无操作）。 */
  release(ownerKey: string): void {
    this.owners.delete(ownerKey);
  }

  releaseAll(): void {
    this.owners.clear();
  }
}

/** 账本维度 → 观察面原因片段（status ring reason 用）。 */
export function budgetDimensionReason(dim: ResourceBudgetDimension): string {
  switch (dim) {
    case 'maxGeometryBytes': return 'budget_exceeded:geometry_bytes';
    case 'maxTextureBytes': return 'budget_exceeded:texture_bytes';
    case 'maxTriangles': return 'budget_exceeded:triangles';
    case 'maxCpuDecodedBytes': return 'budget_exceeded:cpu_decoded_bytes';
    case 'maxMaterials': return 'budget_exceeded:materials';
  }
}

/** 解析统计 → 记账量（CPU 解码按 Float32 展开几何近似）。 */
export function amountsOfStats(stats: {
  triangles: number;
  geometryBytes: number;
  textureBytes: number;
  materialCount: number;
}): ResourceBudgetAmounts {
  return {
    triangles: stats.triangles,
    geometryBytes: stats.geometryBytes,
    textureBytes: stats.textureBytes,
    cpuDecodedBytes: stats.geometryBytes,
    materials: stats.materialCount,
  };
}

/**
 * G04 W1（#1999）：导出时约束版面应用（纯函数，画布导出链专用）。
 *
 * 输入 = 后端 `export_thematic_map` 任务携带的 solve_layout_v5 有界报告
 * （`params.constraint_layout`）；应用到**导出时组件副本** —— 持久
 * MapSpec 零触碰（渲染期事实，不落第二事实源，#2000 同一纪律）。
 *
 * - `placement_zones`：写回组件 `position`（anchor 模式时连同
 *   `placement.anchor`）；浮动件（`placement.mode === 'floating'`）跳过。
 * - `suppressed_ids`（非 key_component、非用户锁）→ `enabled=false`。
 * - 用户锁：`workbench.lockedComponentIds`，`__` 双向族语义（镜像
 *   app/contracts/workbench_locks.py `_lock_matches`）—— 锁 `legend`
 *   保护 `legend__1`。
 * - `not_evaluated` / 缺席 → 原引用返回（字节稳定，不虚构求解）。
 */

export interface ConstraintLayoutReportFE {
  constraint_layout: 'evaluated' | 'not_evaluated' | string;
  page_profile?: string;
  reason?: string;
  placement_zones?: Record<string, string>;
  suppressed_ids?: string[];
  key_component_ids?: string[];
}

interface MapSpecLike {
  workbench?: { lockedComponentIds?: unknown };
  layout?: { components?: unknown };
  [key: string]: unknown;
}

interface ComponentLike {
  id?: unknown;
  enabled?: unknown;
  position?: unknown;
  placement?: unknown;
  [key: string]: unknown;
}

const ANCHORS: ReadonlySet<string> = new Set([
  'top-left', 'top-center', 'top-right',
  'bottom-left', 'bottom-center', 'bottom-right',
]);

/** 锁命中判定（含 `__` 族双向语义，与后端 workbench_locks 同口径）。 */
export function isComponentLocked(componentId: string, locked: ReadonlySet<string>): boolean {
  if (!componentId) return false;
  for (const lockId of locked) {
    if (lockId === componentId) return true;
    if (componentId.startsWith(`${lockId}__`)) return true;
    if (lockId.startsWith(`${componentId}__`)) return true;
  }
  return false;
}

function lockedIdsOf(spec: MapSpecLike): Set<string> {
  const raw = spec?.workbench?.lockedComponentIds;
  if (!Array.isArray(raw)) return new Set();
  return new Set(raw.filter((x): x is string => typeof x === 'string' && !!x));
}

/**
 * 应用求解结果到导出副本。unevaluated / 无组件面 → 返回原引用。
 * 返回值绝不修改输入（deep-copy-on-write：layout.components 逐组件浅拷）。
 */
export function applyConstraintLayoutToSpec(
  spec: MapSpecLike | null | undefined,
  report: ConstraintLayoutReportFE | null | undefined,
): MapSpecLike | null | undefined {
  if (!spec || !report || report.constraint_layout !== 'evaluated') return spec;
  const components = (spec.layout as { components?: ComponentLike[] } | undefined)?.components;
  if (!Array.isArray(components) || components.length === 0) return spec;

  const zones = report.placement_zones ?? {};
  const suppressed = new Set(report.suppressed_ids ?? []);
  const keys = new Set(report.key_component_ids ?? []);
  const locked = lockedIdsOf(spec);

  const nextComponents = components.map((comp) => {
    if (!comp || typeof comp !== 'object') return comp;
    const id = typeof comp.id === 'string' ? comp.id : '';
    const placement =
      comp.placement && typeof comp.placement === 'object'
        ? (comp.placement as Record<string, unknown>)
        : undefined;
    const floating = placement?.mode === 'floating';
    const isLocked = isComponentLocked(id, locked);

    let next: ComponentLike | undefined;
    // 1) 求解区域写回（浮动件与锁实例跳过）
    const zone = zones[id];
    if (!floating && !isLocked && typeof zone === 'string' && ANCHORS.has(zone)) {
      next = { ...comp, position: zone };
      if (placement?.mode === 'anchor') {
        next.placement = { ...placement, anchor: zone };
      }
    }
    // 2) 溢出抑制（key 组件、锁实例与浮动件保持在场 —— 与后端
    //    prepare_publication_document 同语义：浮动件在抑制检查前豁免，
    //    双链同档同图）
    if (suppressed.has(id) && !keys.has(id) && !isLocked && !floating) {
      next = { ...(next ?? comp), enabled: false };
    }
    return next ?? comp;
  });

  const layout = spec.layout as Record<string, unknown> | undefined;
  return {
    ...spec,
    layout: { ...(layout ?? {}), components: nextComponents },
  };
}

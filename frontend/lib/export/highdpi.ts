/**
 * ac-08（ADR-0157）· 高 DPI 导出渲染策略。
 *
 * P1 勘察结论（docs/dev/ac-08-export-recon.md §6）：
 * - `map.setPixelRatio(dpi/96)` 是真重渲染（矢量/符号层细节随分辨率增益），
 *   但栅格瓦片源的取图 zoom 不随 DPR 变化（tilezoom-dpr-probe 实证：
 *   DPR 1 与 3.125 均取同一 zoom）→ 栅格细节增益由矢量引擎（SVG 孪生
 *   oversample ≤2 级）承担，canvas 路径如实披露（诊断码，不静默）。
 * - #527：idle 等待必须有界。本模块把「超时」从**失败**升级为 §0.5 契约的
 *   **降级**：恢复原始 pixelRatio，等一次短界重绘后以当前画布导出 +
 *   `highdpi_rerender_timeout_degraded` 诊断（导出不再因超时整体失败）。
 */
import type { ExportDegradation } from '../map-kit/export-chrome';

/** #527：idle 等待截止（与 exportCommands 队列级看门狗互不替代）。 */
export const EXPORT_IDLE_TIMEOUT_MS = 30_000;

/** 降级回退后等待画布重绘的短截止（超时则如实失败 —— 无法捕获即无法导出）。 */
export const DEGRADE_REPAINT_TIMEOUT_MS = 3_000;

/**
 * 看门狗预算（review：降级链路必须完整落在队列看门狗之内）：
 * - fit 窗口：prepareWysiwygCamera 的 idle 截止硬顶 5s（Math.min(_, 5_000)）；
 * - 余量：上传前合成/画布抓取等非 idle 开销的保险。
 * 30s 看门狗的最坏链路 = idle(20s) + 降级重绘(3s) + fit(5s) = 28s < 30s，
 * 降级才有机会在看门狗 settle 'timeout' 之前完成并回传真实结果。
 */
export const WATCHDOG_FIT_BUDGET_MS = 5_000;
export const WATCHDOG_MARGIN_MS = 2_000;
/** 预算下限：低于此值 idle 截止失去等待意义（慢机器上直接走降级也比挂死好）。 */
export const MIN_IDLE_TIMEOUT_MS = 5_000;

/**
 * 把高 DPI idle 截止预算进队列看门狗内：
 * `max(MIN_IDLE_TIMEOUT_MS, watchdog − 降级重绘 − fit − 余量)`。
 * export_map 命令路径用它替换默认 EXPORT_IDLE_TIMEOUT_MS —— 否则 30s 看门狗
 * 先 settle 'timeout'，"idle 超时 → 降级导出"在永不 idle 的场景永远没机会完成。
 */
export function idleTimeoutWithinWatchdog(watchdogTimeoutMs: number): number {
  return Math.max(
    MIN_IDLE_TIMEOUT_MS,
    watchdogTimeoutMs - DEGRADE_REPAINT_TIMEOUT_MS - WATCHDOG_FIT_BUDGET_MS - WATCHDOG_MARGIN_MS,
  );
}

/** #527：idle 等待超时的类型化错误 —— catch 可识别并给出如实的失败文案。 */
export class MapIdleTimeoutError extends Error {
  constructor(timeoutMs: number, phase: 'rerender' | 'degraded-repaint' = 'rerender') {
    super(
      phase === 'rerender'
        ? `导出中止：地图在 ${timeoutMs}ms 内未进入 idle 状态` +
            `（可能 WebGL 上下文已丢失或画布被隐藏）`
        : `导出中止：高 DPI 超时降级回退后，画布在 ${timeoutMs}ms 内未完成重绘，` +
            `无法捕获可用画面`,
    );
    this.name = 'MapIdleTimeoutError';
  }
}

/** 有界等待 `map.once('idle')`：idle 触发即 resolve，截止前未触发即 reject。 */
export async function waitForMapIdle(map: HighDpiMapLike, timeoutMs: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new MapIdleTimeoutError(timeoutMs)), timeoutMs);
    map.once('idle', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/** 单飞：引擎级互斥所需的最小 map 面（仅类型约束，运行时不依赖 maplibre）。 */
export interface HighDpiMapLike {
  getPixelRatio(): number;
  setPixelRatio(ratio: number): void;
  once(type: 'idle', listener: () => void): unknown;
  getStyle?: () => { sources?: Record<string, { type?: string }> } | undefined | null;
}

export type HighDpiRenderMode = 'rerendered' | 'degraded-native' | 'skipped';

export interface HighDpiRenderResult {
  mode: HighDpiRenderMode;
  degradations: ExportDegradation[];
}

/** 列出 style 中的栅格瓦片源（含越界容错 —— 测试环境可能无 style 面）。 */
export function detectRasterSourceIds(map: HighDpiMapLike): string[] {
  let sources: Record<string, { type?: string }> | undefined | null;
  try {
    sources = map.getStyle?.()?.sources;
  } catch {
    sources = undefined;
  }
  if (!sources) return [];
  return Object.entries(sources)
    .filter(([, def]) => def && def.type === 'raster')
    .map(([id]) => id);
}

/**
 * 进入高 DPI 重渲染。语义：
 * - dpi ≤ 96 → skipped（原生比率已是目标）。
 * - setPixelRatio + 有界 idle → rerendered；栅格源存在时附 info 诊断。
 * - idle 超时 → 立即恢复原始比率，等一次短界重绘后以 degraded-native 返回
 *   （诊断码 highdpi_rerender_timeout_degraded）；若回退重绘也超时 →
 *   类型化失败（无画面可捕获，不伪造产物）。
 * 恢复原始比率的兜底仍在调用方 finally（本函数异常路径已自行恢复）。
 */
export async function enterHighDpiRender(
  map: HighDpiMapLike,
  dpi: number,
  idleTimeoutMs?: number,
): Promise<HighDpiRenderResult> {
  const targetPixelRatio = dpi / 96;
  const origPixelRatio = map.getPixelRatio();
  if (targetPixelRatio <= 1) {
    return { mode: 'skipped', degradations: [] };
  }
  try {
    map.setPixelRatio(targetPixelRatio);
    await waitForMapIdle(map, idleTimeoutMs ?? EXPORT_IDLE_TIMEOUT_MS);
  } catch (e) {
    if (!(e instanceof MapIdleTimeoutError)) throw e;
    // §0.5 降级契约：回退当前分辨率画布导出，而不是整体失败。
    map.setPixelRatio(origPixelRatio);
    await waitForMapIdle(map, DEGRADE_REPAINT_TIMEOUT_MS).catch((repaintErr) => {
      if (repaintErr instanceof MapIdleTimeoutError) {
        throw new MapIdleTimeoutError(DEGRADE_REPAINT_TIMEOUT_MS, 'degraded-repaint');
      }
      throw repaintErr;
    });
    return {
      mode: 'degraded-native',
      degradations: [
        {
          code: 'highdpi_rerender_timeout_degraded',
          detail: `${dpi}DPI 重渲染等待超时，已回退当前分辨率画布`,
        },
      ],
    };
  }
  const rasterIds = detectRasterSourceIds(map);
  const degradations: ExportDegradation[] = rasterIds.length
    ? [
        {
          code: 'raster_tile_detail_limited_highdpi',
          detail: `栅格瓦片源（${rasterIds.slice(0, 3).join('、')}${rasterIds.length > 3 ? ' 等' : ''}）按原 zoom 取图，细节不随 ${dpi}DPI 提升`,
        },
      ]
    : [];
  return { mode: 'rerendered', degradations };
}

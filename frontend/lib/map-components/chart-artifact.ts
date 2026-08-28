import { apiFetch } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { adaptChartData } from '@/lib/chart-adapter';
import { devOnly } from '@/lib/utils/logger';
import type { ChartData } from '@/lib/types';

/**
 * chart_panel 的 ref 数据通道（D2）。
 *
 * 大数据/派生图表不 inline 进 MapSpec（options.chart 100KB/500pts 上限），
 * 而是存 session_data_manager，MapSpec 只持 `ref:chart-*` 引用。本模块按
 * ref 拉取 GET /chat/sessions/{sid}/chart-artifacts/{ref} 并经
 * adaptChartData 复用 chat 同一 ChartData 校验契约。
 *
 * 模式照 ref-source-resolver.ts：模块级缓存 + in-flight 去重 + 代数订阅
 * （同一 ref 多面板挂载只发一次请求）；会话切换由调用方语义决定重取
 * （ref 归会话所有，resetChartArtifactCache 清空）。
 */

const cache = new Map<string, ChartData | null>(); // null = 拉取失败/非法载荷
const failureAt = new Map<string, number>(); // #1078(G-4): 失败时间戳（TTL 重试窗口）
const inFlight = new Map<string, Promise<ChartData | null>>();
let generation = 0;
const listeners = new Set<() => void>();
// #1078(G-4): 失败不再永久缓存 —— 瞬时 500/超时让降级卡片在 TTL 后可重试。
const FAILURE_TTL_MS = 30_000;

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeChartArtifacts(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getChartArtifactsGeneration(): number {
  return generation;
}

/** 清空缓存（会话切换 / 测试隔离）。 */
export function resetChartArtifactCache(): void {
  cache.clear();
  failureAt.clear();
  inFlight.clear();
  emit();
}

/** 同步读缓存：ChartData（命中）/ null（失败）/ undefined（未拉取）。
 * #1078(G-4): null（失败）条目超过 FAILURE_TTL_MS 视为未拉取（可重试）。
 */
export function getCachedChartArtifact(ref: string): ChartData | null | undefined {
  const hit = cache.get(ref);
  if (hit === null) {
    const at = failureAt.get(ref) ?? 0;
    if (Date.now() - at > FAILURE_TTL_MS) {
      cache.delete(ref);
      failureAt.delete(ref);
      return undefined;
    }
  }
  return hit;
}

/**
 * 按 ref 拉取图表 artifact。缓存命中直接返回；失败返回 null（不抛错 ——
 * 调用方渲染「图表数据不可用」降级卡片，绝不崩 chrome）。
 */
export function loadChartArtifact(ref: string): Promise<ChartData | null> {
  const key = ref.trim();
  if (!key) return Promise.resolve(null);
  const hit = cache.get(key);
  if (hit !== undefined) return Promise.resolve(hit);
  const pending = inFlight.get(key);
  if (pending) return pending;

  const task = (async (): Promise<ChartData | null> => {
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    // 无会话（本地 chrome / 测试）：不缓存 —— 会话 cursor 就位后可重试
    if (!sessionId) return null;
    try {
      const data = await apiFetch<{ chart?: unknown }>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/chart-artifacts/${encodeURIComponent(key)}`,
        { ownerToken, label: 'Chart artifact resolve error' },
      );
      // 响应契约 {chart: ChartData}；边界处仍走 adaptChartData（与 chat
      // inline 路径同一校验，不允许第二套图表 schema 溜进来）
      const chart = adaptChartData((data as { chart?: unknown } | null)?.chart);
      cache.set(key, chart);
      return chart;
    } catch (e) {
      devOnly.warn(`[chart-artifact] ref ${key} 拉取失败`, e);
      cache.set(key, null);
      failureAt.set(key, Date.now());
      return null;
    }
  })();
  inFlight.set(key, task);
  // 清理经 microtask 挂尾（同步完成路径下 inFlight.set 先于清理执行，
  // 不留已完成的占位 promise）
  const cleanup = () => {
    inFlight.delete(key);
    emit();
  };
  void task.then(cleanup, cleanup);
  return task;
}

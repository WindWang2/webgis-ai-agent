/**
 * 系统健康面 typed client（ADR-0142 D6）。
 *
 * 端点形状逐字对照 master（app/api/routes/health.py、version.py、metrics.py）：
 * - GET /api/v1/health          无认证基础卡
 * - GET /api/v1/health/live     存活探针
 * - GET /api/v1/ready           就绪（503 body 极简）
 * - GET /api/v1/status/detailed JWT 组件级（db/redis/llm/worker/object_store）
 * - GET /api/v1/version         构建/扩展 API 信息
 * - GET /api/v1/metrics/digest  require_admin 工具耗时指标（非错误分类）
 *
 * 注意：/healthz 不存在；/metrics 是根路径 Prometheus 文本（前端不消费——
 * 文本格式解析不属于控制台职责，组件状态走 /status/detailed）。
 */
import { apiFetch } from './transport';

export interface HealthBasic {
  status: string;
  timestamp: string;
  service: string;
  version: string;
  agent_runtime: 'pi' | 'chatengine' | string;
  pi_workers_alive: string | null;
}

export interface LiveProbe {
  status: string;
}

export interface ComponentHealth {
  status: 'ok' | 'degraded' | 'down' | 'not_configured';
  latency_ms: number | null;
  detail: string | null;
}

export interface DetailedStatus {
  status: 'ok' | 'degraded' | 'down';
  components: Record<string, ComponentHealth>;
  stuck_jobs: number | null;
  refresh_age_s: number;
}

export interface VersionInfo {
  version: string;
  commit: string;
  python: string;
  extensions_api: string;
  timestamp: string;
}

export interface MetricsDigest {
  success: boolean;
  tool_metrics: Record<string, unknown>;
  spatial_cache: Record<string, unknown>;
  harness_enabled: boolean;
  harness_metrics: Record<string, unknown>;
}

export async function getHealthBasic(opts: { signal?: AbortSignal } = {}): Promise<HealthBasic> {
  return apiFetch<HealthBasic>('/api/v1/health', { signal: opts.signal, skipAuth: true });
}

export async function getLiveProbe(opts: { signal?: AbortSignal } = {}): Promise<LiveProbe> {
  return apiFetch<LiveProbe>('/api/v1/health/live', { signal: opts.signal, skipAuth: true });
}

export async function getReadyProbe(opts: { signal?: AbortSignal } = {}): Promise<{ ready: boolean }> {
  return apiFetch<{ ready: boolean }>('/api/v1/ready', { signal: opts.signal, skipAuth: true });
}

export async function getDetailedStatus(
  opts: { signal?: AbortSignal } = {},
): Promise<DetailedStatus> {
  return apiFetch<DetailedStatus>('/api/v1/status/detailed', { signal: opts.signal });
}

export async function getVersionInfo(opts: { signal?: AbortSignal } = {}): Promise<VersionInfo> {
  return apiFetch<VersionInfo>('/api/v1/version', { signal: opts.signal, skipAuth: true });
}

export async function getMetricsDigest(opts: { signal?: AbortSignal } = {}): Promise<MetricsDigest> {
  return apiFetch<MetricsDigest>('/api/v1/metrics/digest', { signal: opts.signal });
}

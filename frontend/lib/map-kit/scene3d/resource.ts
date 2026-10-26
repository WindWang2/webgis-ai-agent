/**
 * scene3d/resource — 受控资源加载策略（G3D02 M3）。
 *
 * 纪律（工作单 §0.8 SSRF/预算约束）：
 * - URL 只放行 https: 与同源相对路径；http:/file:/data: 等一律 typed 拒绝
 *   （白名单防线在前端；服务端 SSRF 责任不因此豁免）。
 * - 一切请求带 AbortController；超时可配；响应体积上限（JSON 2MB /
 *   二进制 64MB）超限 typed 拒绝，绝不 partial-accept。
 * - 不内嵌任何凭据/API Key；credentials: 'omit'。
 */

export const MAX_JSON_BYTES = 2 * 1024 * 1024;
export const MAX_BINARY_BYTES = 64 * 1024 * 1024;
export const DEFAULT_TIMEOUT_MS = 30_000;

export type Scene3dResourceError =
  | 'url_rejected'
  | 'network_error'
  | 'timeout'
  | 'aborted'
  | 'http_error'
  | 'payload_too_large'
  | 'invalid_payload';

export class Scene3dResourceFailure extends Error {
  readonly code: Scene3dResourceError;
  readonly url: string;
  constructor(code: Scene3dResourceError, url: string, detail?: string) {
    super(`scene3d resource ${code}: ${url}${detail ? ` (${detail})` : ''}`);
    this.name = 'Scene3dResourceFailure';
    this.code = code;
    this.url = url;
  }
}

/** URL 白名单：https 绝对地址或同源相对路径（协议相对 `//` 拒绝）。 */
export function isAllowedUrl(raw: string): boolean {
  if (typeof raw !== 'string' || raw.length === 0) return false;
  if (raw.includes('\\') || /[\u0000-\u001f]/.test(raw)) return false;
  if (raw.startsWith('//')) return false;
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
    return raw.toLowerCase().startsWith('https:');
  }
  // 相对路径（含 /path、./path、../path —— 同源）
  return true;
}

/** 基 URL 解析（tile 相对 URI 消费）；非法输入 → null（不抛）。 */
export function resolveUri(base: string, uri: string): string | null {
  if (!isAllowedUrl(uri)) return null;
  try {
    const resolved = new URL(uri, new URL(base, typeof location !== 'undefined' ? location.href : 'https://scene3d.local/'));
    if (resolved.protocol !== 'https:' && resolved.origin !== (typeof location !== 'undefined' ? location.origin : '')) {
      // 非 https 且非同源 → 拒绝
      if (resolved.protocol !== 'https:') return null;
    }
    return resolved.toString();
  } catch {
    return null;
  }
}

export interface FetchOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  maxBytes?: number;
}

/**
 * 带预算的响应体读取（review P1：content-length 可省略/伪造 —— 声明头
 * 只是快路径；body 必须流式累计，超限 cancel）。超时覆盖到 body 读完。
 */
async function readBodyWithBudget(
  res: Response,
  url: string,
  maxBytes: number,
  controller: AbortController,
  timeoutMs: number,
  outerSignal: AbortSignal | undefined,
): Promise<ArrayBuffer> {
  const timer = setTimeout(
    () => controller.abort(new DOMException('scene3d timeout', 'TimeoutError')),
    timeoutMs,
  );
  const onOuterAbort = () => controller.abort(outerSignal?.reason);
  outerSignal?.addEventListener('abort', onOuterAbort, { once: true });
  try {
    if (res.body && typeof res.body.getReader === 'function') {
      const reader = res.body.getReader();
      const chunks: Uint8Array[] = [];
      let total = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!value) continue;
        total += value.byteLength;
        if (total > maxBytes) {
          await reader.cancel().catch(() => undefined);
          throw new Scene3dResourceFailure('payload_too_large', url, `streamed ${total} > ${maxBytes}`);
        }
        chunks.push(value);
      }
      const out = new Uint8Array(total);
      let off = 0;
      for (const c of chunks) {
        out.set(c, off);
        off += c.byteLength;
      }
      return out.buffer;
    }
    // 无流 API 的环境兜底（node polyfill 路径）
    const buf = await res.arrayBuffer();
    if (buf.byteLength > maxBytes) {
      throw new Scene3dResourceFailure('payload_too_large', url, `${buf.byteLength} > ${maxBytes}`);
    }
    return buf;
  } catch (e) {
    if (e instanceof Scene3dResourceFailure) throw e;
    if (outerSignal?.aborted) throw new Scene3dResourceFailure('aborted', url);
    if (e instanceof DOMException && (e.name === 'TimeoutError' || e.name === 'AbortError')) {
      throw new Scene3dResourceFailure('timeout', url);
    }
    throw new Scene3dResourceFailure('network_error', url);
  } finally {
    clearTimeout(timer);
    outerSignal?.removeEventListener('abort', onOuterAbort);
  }
}

async function fetchWithBudget(
  url: string,
  kind: 'json' | 'binary',
  opts: FetchOptions = {},
): Promise<{ res: Response; body: () => Promise<ArrayBuffer> }> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxBytes = opts.maxBytes ?? (kind === 'json' ? MAX_JSON_BYTES : MAX_BINARY_BYTES);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException('scene3d timeout', 'TimeoutError')), timeoutMs);
  const onOuterAbort = () => controller.abort(opts.signal?.reason);
  opts.signal?.addEventListener('abort', onOuterAbort, { once: true });
  if (opts.signal?.aborted) {
    clearTimeout(timer);
    throw new Scene3dResourceFailure('aborted', url);
  }
  let res: Response;
  try {
    res = await fetch(url, {
      signal: controller.signal,
      credentials: 'omit',
      redirect: 'error', // 跨协议/多跳重定向不跟随（预算可控）
      headers: kind === 'json' ? { accept: 'application/json' } : undefined,
    });
  } catch (e) {
    if (opts.signal?.aborted) throw new Scene3dResourceFailure('aborted', url);
    if (e instanceof DOMException && (e.name === 'TimeoutError' || e.name === 'AbortError')) {
      throw new Scene3dResourceFailure('timeout', url);
    }
    throw new Scene3dResourceFailure('network_error', url);
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener('abort', onOuterAbort);
  }
  if (!res.ok) {
    throw new Scene3dResourceFailure('http_error', url, `status ${res.status}`);
  }
  const declared = Number(res.headers.get('content-length') ?? '0');
  if (declared && declared > maxBytes) {
    throw new Scene3dResourceFailure('payload_too_large', url, `${declared} > ${maxBytes}`);
  }
  return {
    res,
    body: () => readBodyWithBudget(res, url, maxBytes, controller, timeoutMs, opts.signal),
  };
}

export async function fetchJson<T = unknown>(url: string, opts: FetchOptions = {}): Promise<T> {
  const { body } = await fetchWithBudget(url, 'json', opts);
  const buf = await body();
  try {
    return JSON.parse(new TextDecoder().decode(buf)) as T;
  } catch {
    throw new Scene3dResourceFailure('invalid_payload', url, 'not JSON');
  }
}

export async function fetchBinary(url: string, opts: FetchOptions = {}): Promise<ArrayBuffer> {
  const { body } = await fetchWithBudget(url, 'binary', opts);
  return body();
}

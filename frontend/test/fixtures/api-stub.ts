/**
 * api-stub —— 零依赖的路由表式 fetch 拦截器（msw 等价物，ADR-0142 D4）。
 *
 * 仓库不存在 msw（P0 勘测：package.json 无依赖、测试纪律是显式 stub），
 * 10 线并发下不为单线引入新依赖。本模块提供等价能力：
 *
 *   const stub = installApiStub();
 *   stub.on('GET', '/api/v1/geocompute/cluster/metrics', stateful({
 *     ok:   metricsFixture,          // 正常态
 *     empty: emptyMetricsFixture,    // 空态（诚实零值）
 *     error: { status: 403, body: { detail: 'Admin privileges required' } },
 *   }));
 *   stub.mode('ok');                 // 全局切态（组件三态测试）
 *   stub.calls;                      // 请求记录（轮询纪律断言：请求次数/间隔）
 *   stub.restore();
 *
 * 只在 vitest/jsdom 中使用；生产 bundle 不得 import（§6 自审项）。
 */
import { vi } from 'vitest';

export type FixtureState = 'ok' | 'empty' | 'error';

export interface StubRequestContext {
  url: URL;
  search: URLSearchParams;
  body: unknown;
}

export interface StubResponse {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
}

/** handler 返回值：直接作为 JSON body（200），或包 {status, body}。 */
export type StubHandlerResult = unknown;
export type StubHandler = (ctx: StubRequestContext) => StubHandlerResult | Promise<StubHandlerResult>;

interface RouteEntry {
  method: string;
  match: string | RegExp;
  ok: StubHandler;
  empty: StubHandler;
  error: StubHandler;
}

function statusOf(result: StubHandlerResult): StubResponse {
  if (result && typeof result === 'object' && 'status' in (result as Record<string, unknown>)
    && 'body' in (result as Record<string, unknown>)) {
    return result as StubResponse;
  }
  return { status: 200, body: result };
}

/** 三态 handler 打包：按 stub 当前全局态选择 fixture；fixture 若是函数则透传 ctx。 */
export function stateful(fixtures: {
  ok: StubHandlerResult | StubHandler;
  empty: StubHandlerResult | StubHandler;
  error: StubHandlerResult | StubHandler;
}): Record<FixtureState, StubHandler> {
  const wrap = (v: StubHandlerResult | StubHandler): StubHandler =>
    typeof v === 'function' ? (v as StubHandler) : () => v;
  return {
    ok: wrap(fixtures.ok),
    empty: wrap(fixtures.empty),
    error: wrap(fixtures.error),
  };
}

export class ApiStub {
  readonly routes: RouteEntry[] = [];
  readonly calls: { method: string; path: string; body: unknown }[] = [];
  private state: FixtureState = 'ok';
  private originalFetch: typeof fetch | null = null;
  private fetchMock: ReturnType<typeof vi.fn> | null = null;

  /** 注册三态路由（同 method+match 重复注册时替换既有 handler）。 */
  on(method: string, match: string | RegExp, handlers: Record<FixtureState, StubHandler>): this {
    const m = method.toUpperCase();
    const existing = this.routes.find(
      (r) => r.method === m && String(r.match) === String(match),
    );
    if (existing) {
      existing.ok = handlers.ok;
      existing.empty = handlers.empty;
      existing.error = handlers.error;
      return this;
    }
    this.routes.push({ method: m, match, ...handlers });
    return this;
  }

  /** 注册固定响应路由（不随全局态切换）。 */
  always(method: string, match: string | RegExp, handler: StubHandler): this {
    return this.on(method, match, { ok: handler, empty: handler, error: handler });
  }

  mode(state: FixtureState): this {
    this.state = state;
    return this;
  }

  private routeFor(method: string, pathname: string): RouteEntry | undefined {
    const m = method.toUpperCase();
    const byMethod = this.routes.filter((r) => r.method === m);
    // 优先级：字符串精确 > 正则 > 前缀 —— 避免 /runs 吞掉 /runs/{id}/events。
    return (
      byMethod.find((r) => typeof r.match === 'string' && pathname === r.match) ??
      byMethod.find((r) => r.match instanceof RegExp && r.match.test(pathname)) ??
      byMethod.find((r) => typeof r.match === 'string' && pathname.startsWith(r.match))
    );
  }

  /** 安装全局 fetch 拦截（幂等；先 restore 再装）。 */
  install(): this {
    this.restore();
    this.originalFetch = globalThis.fetch;
    this.fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      const method = (init?.method ?? 'GET').toUpperCase();
      let body: unknown = null;
      if (typeof init?.body === 'string') {
        try {
          body = JSON.parse(init.body);
        } catch {
          body = init.body;
        }
      }
      this.calls.push({ method, path: url.pathname + url.search, body });

      const route = this.routeFor(method, url.pathname);
      if (!route) {
        return new Response(JSON.stringify({ detail: `api-stub: no route for ${method} ${url.pathname}` }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const handler = route[this.state];
      const result = statusOf(await handler({ url, search: url.searchParams, body }));
      return new Response(JSON.stringify(result.body ?? null), {
        status: result.status,
        headers: { 'Content-Type': 'application/json', ...(result.headers ?? {}) },
      });
    }) as ReturnType<typeof vi.fn>;
    globalThis.fetch = this.fetchMock as unknown as typeof fetch;
    return this;
  }

  restore(): void {
    if (this.originalFetch) {
      globalThis.fetch = this.originalFetch;
      this.originalFetch = null;
      this.fetchMock = null;
    }
  }

  /** 清空调用记录（保留路由）。 */
  resetCalls(): void {
    this.calls.length = 0;
  }
}

export function installApiStub(): ApiStub {
  return new ApiStub().install();
}

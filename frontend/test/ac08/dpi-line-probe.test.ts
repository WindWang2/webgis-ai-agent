/**
 * dpi-line-probe 回归（P3-frontend_scripts-00）。
 *
 * 1) 未传 --dpi 但传了其它参数时，旧实现用 args[indexOf('--dpi')+1] 取到
 *    args[0]，DPR 列表解析为空 → 静默只产出 baseline96。
 * 2) pageerror 监听器内直接 throw 会变成 uncaught，finally 不执行 →
 *    chromium 进程与临时 HTML 泄漏；改为先收集、再在受控位置抛出。
 */

import { EventEmitter } from 'node:events';
import { beforeAll, describe, expect, it } from 'vitest';

type ResolveDpiArg = (args: string[]) => string;
type PageErrorCollector = { throwIfAny: () => void };
type CollectPageErrors = (page: EventEmitter) => PageErrorCollector;

let resolveDpiArg: ResolveDpiArg;
let collectPageErrors: CollectPageErrors;

beforeAll(async () => {
  const mod = (await import('../../scripts/ac08/dpi-line-probe.mjs')) as unknown as {
    resolveDpiArg: ResolveDpiArg;
    collectPageErrors: CollectPageErrors;
  };
  resolveDpiArg = mod.resolveDpiArg;
  collectPageErrors = mod.collectPageErrors;
});

describe('dpi-line-probe resolveDpiArg', () => {
  it('未传 --dpi 时回落 96,300，不被其它参数污染', () => {
    expect(resolveDpiArg([])).toBe('96,300');
    expect(resolveDpiArg(['--out', 'docs/x'])).toBe('96,300');
  });

  it('支持 --dpi=a,b 与 --dpi a,b 两种写法', () => {
    expect(resolveDpiArg(['--dpi=150,300'])).toBe('150,300');
    expect(resolveDpiArg(['--out', 'docs/x', '--dpi', '150,300'])).toBe('150,300');
  });
});

describe('dpi-line-probe collectPageErrors', () => {
  it('pageerror 监听器不抛异常，延迟到 throwIfAny', () => {
    const page = new EventEmitter();
    const collector = collectPageErrors(page);

    expect(() => page.emit('pageerror', new Error('boom'))).not.toThrow();
    expect(() => collector.throwIfAny()).toThrow('pageerror: boom');
  });

  it('无 pageerror 时 throwIfAny 不抛', () => {
    const collector = collectPageErrors(new EventEmitter());

    expect(() => collector.throwIfAny()).not.toThrow();
  });
});
